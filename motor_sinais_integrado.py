#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pipeline integrado de sinais XAUUSD. Usa somente as oito tabelas oficiais."""
import bisect, json, math, os, sqlite3, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parent
DB=ROOT/"data"/"xauusd.db"
MODEL=ROOT/"data"/"modelo_gemini.json"
TABLES={"m5","m15","h1","indicadores","tabuleiro","sinais_validados","sinais_negados","ranking"}
TF={"M5":5,"M15":15,"H1":60}
WINDOW=48
HORIZON=3
PESO_TECNICO=0.75
PESO_GEMINI=0.25
LIMITE_APROVACAO=40.0
GEMINI=os.getenv("GEMINI_API_KEY","").strip()
TG_TOKEN=os.getenv("TELEGRAM_BOT_TOKEN","").strip()
TG_CHAT=os.getenv("TELEGRAM_CHAT_ID","").strip()
API="https://generativelanguage.googleapis.com/v1beta"

def now(): return datetime.now(timezone.utc)
def stamp(): return now().isoformat(timespec="seconds")
def dt(v):
    x=datetime.fromisoformat(str(v).replace("Z","+00:00"))
    return x.astimezone(timezone.utc).replace(tzinfo=None) if x.tzinfo else x
def n(v):
    try:
        x=float(str(v).replace(",","."))
        return x if math.isfinite(x) else None
    except (TypeError,ValueError): return None
def percentile(values,p):
    a=sorted(x for x in values if x is not None and math.isfinite(x))
    if not a:return 0.0
    q=(len(a)-1)*p; lo=int(q); hi=min(lo+1,len(a)-1)
    return a[lo]+(a[hi]-a[lo])*(q-lo)
def db_open():
    if not DB.is_file(): raise FileNotFoundError("Banco oficial data/xauusd.db ausente.")
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row
    names={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    if names!=TABLES: raise RuntimeError("Banco fora do esquema: sao exigidas exatamente as oito tabelas oficiais. Encontradas: "+str(sorted(names)))
    return c

def load_readings(c):
    candles=[dict(x) for x in c.execute("SELECT data_hora,fechamento FROM m5 ORDER BY data_hora")]
    if len(candles)<151: raise RuntimeError(f"Historico M5 insuficiente: {len(candles)} velas.")
    indicators=[dict(x) for x in c.execute("SELECT timeframe,data,hora,preco,ema9,ema21,rsi,macd,atr FROM indicadores WHERE timeframe IN ('M5','M15','H1')")]
    indexes={}
    for tf in TF:
        pairs=[]
        for x in indicators:
            if x["timeframe"]==tf:
                try:pairs.append((dt(str(x["data"])+"T"+str(x["hora"])),x))
                except Exception:pass
        pairs.sort(key=lambda z:z[0]); indexes[tf]=([z[0] for z in pairs],[z[1] for z in pairs])
    raw_rows=[]; current=now().replace(tzinfo=None)
    for candle in candles:
        opened=dt(candle["data_hora"]); closed=opened+timedelta(minutes=5)
        if closed>current:continue
        price=n(candle["fechamento"])
        if price is None or price<=0:raise RuntimeError("Fechamento M5 invalido em "+str(candle["data_hora"]))
        selected={}
        for tf,minutes in TF.items():
            times,rows=indexes[tf]
            pos=bisect.bisect_right(times,closed-timedelta(minutes=minutes))-1
            selected[tf]=rows[pos] if pos>=0 else None
        raw={}
        for tf,row in selected.items():
            if not row:continue
            e9=n(row.get("ema9")); e21=n(row.get("ema21")); rsi=n(row.get("rsi")); macd=n(row.get("macd"))
            if e9 is not None and e21 is not None:raw[tf+"_EMA"]=(e9-e21)/price*10000
            if rsi is not None:raw[tf+"_RSI"]=rsi-50
            if macd is not None:raw[tf+"_MACD"]=macd/price*10000
        raw_rows.append({"timestamp":candle["data_hora"],"closed":closed,"price":price,"raw":raw,"indicators":selected})
    if len(raw_rows)<51:raise RuntimeError("Historico de velas fechadas insuficiente.")
    for a,b in zip(raw_rows[-48:-1],raw_rows[-47:]):
        if b["closed"]-a["closed"]!=timedelta(minutes=5):
            raise RuntimeError(f"Lacuna M5 necessaria: {a['timestamp']} -> {b['timestamp']}. Interrompido sem inventar velas.")
    readings=[]
    for i,row in enumerate(raw_rows):
        if i+1<WINDOW:continue
        history=[z["raw"] for z in raw_rows[i-WINDOW+1:i+1]]
        features={}
        for key,value in row["raw"].items():
            scale=percentile([abs(n(h.get(key))) for h in history if n(h.get(key)) is not None],0.85)
            intensity=0 if scale<=1e-12 else min(10,abs(value)/scale*10)
            features[key]=round(intensity if value>0 else -intensity,4)
        if len(features)>=3:
            readings.append({"timestamp":row["timestamp"],"closed":row["closed"],"price":row["price"],"features":features,"indicators":row["indicators"]})
    if len(readings)<103:raise RuntimeError(f"Leituras tecnicas validas insuficientes: {len(readings)}.")
    return readings

def score(features):
    buy=sell=0.0; detail={}
    for key,value in features.items():
        v=n(value)
        if v is None:continue
        contribution=max(-10,min(10,v))
        if contribution>0:buy+=contribution
        elif contribution<0:sell+=abs(contribution)
        detail[key]={"nota_assinada":round(v,4),"peso":1,"contribuicao":round(contribution,4)}
    return {"nota_long":round(buy,4),"nota_short":round(sell,4),"score_final":round(buy-sell,4),"indicadores":detail}

def make_samples(readings):
    out=[]
    for i in range(len(readings)-HORIZON):
        out.append({"features":readings[i]["features"],"retorno":readings[i+HORIZON]["price"]-readings[i]["price"]})
    if len(out)<100:raise RuntimeError("Amostras historicas insuficientes.")
    return out
def evaluate(samples,trigger,direction):
    chosen=[]; wins=0; returns=[]
    for item in samples:
        value=score(item["features"])["score_final"]
        match=(direction=="LONG" and value>=trigger) or (direction=="SHORT" and value<=-trigger)
        if match:
            chosen.append(item); returns.append(item["retorno"])
            if (direction=="LONG" and item["retorno"]>0) or (direction=="SHORT" and item["retorno"]<0):wins+=1
    total=len(chosen); rate=wins/total if total else 0; coverage=total/len(samples) if samples else 0
    return {"direcao":direction,"gatilho":trigger,"amostras":len(samples),"sinais":total,"acertos":wins,"erros":total-wins,"taxa_acerto":round(rate,6),"cobertura":round(coverage,6),"media_movimento_preco":round(sum(returns)/total,6) if total else 0}
def calibrate(samples,direction):
    candidates=[]
    for trigger in range(5,91):
        result=evaluate(samples,trigger,direction)
        if result["sinais"]<50:continue
        confidence=math.sqrt(result["sinais"]/(result["sinais"]+100))
        result["objetivo_calibracao"]=round((result["taxa_acerto"]-.5)*math.sqrt(result["cobertura"])*confidence,8)
        candidates.append(result)
    if not candidates:raise RuntimeError("Nenhum gatilho com 50 sinais de treino para "+direction)
    above=[x for x in candidates if x["taxa_acerto"]>=.5]
    best=max(above,key=lambda x:(x["objetivo_calibracao"],x["taxa_acerto"],x["sinais"],x["cobertura"])) if above else max(candidates,key=lambda x:(x["taxa_acerto"],x["sinais"],x["cobertura"]))
    return best["gatilho"],best
def calibrate_validate(readings):
    samples=make_samples(readings); cut=int(len(samples)*.70)
    train=samples[:max(0,cut-HORIZON)]; validation=samples[cut:]
    if len(train)<100:raise RuntimeError(f"Treino insuficiente apos purga: {len(train)}")
    if len(validation)<40:raise RuntimeError(f"Validacao insuficiente: {len(validation)}")
    long_trigger,long_train=calibrate(train,"LONG"); short_trigger,short_train=calibrate(train,"SHORT")
    long_val=evaluate(validation,long_trigger,"LONG"); short_val=evaluate(validation,short_trigger,"SHORT")
    approved=long_val["sinais"]>=20 and short_val["sinais"]>=20 and long_val["taxa_acerto"]>=.5 and short_val["taxa_acerto"]>=.5
    return {"approved":approved,"long":long_trigger,"short":short_trigger,"train":{"long":long_train,"short":short_train,"total":len(train)},"validation":{"long":long_val,"short":short_val,"total":len(validation)},"features":sorted({k for x in train for k in x["features"]})}

def board_context(c):
    out=[]
    for row in c.execute("SELECT * FROM tabuleiro ORDER BY data_hora DESC LIMIT 30"):
        x=dict(row)
        if x.get("sequencia_json"):
            try:x["sequencia_json"]=json.loads(x["sequencia_json"])
            except Exception:pass
        out.append(x)
    return out
def get_models():
    if not MODEL.is_file():raise RuntimeError("Artifact data/modelo_gemini.json do Testador nao encontrado.")
    with open(MODEL,encoding="utf-8") as f:data=json.load(f)
    if str(data.get("status","")).lower() not in ("pronto","pronto_com_aviso_api"):raise RuntimeError("Testador Gemini nao esta pronto: "+str(data.get("status")))
    chosen=str(data.get("modelo_escolhido") or "").strip()
    if not chosen:raise RuntimeError("Testador nao informou modelo_escolhido.")
    models=[chosen]
    for item in data.get("modelos_rankeados",[]):
        name=str(item.get("modelo") or "").strip()
        if name.startswith("models/gemini-") and int(item.get("sucessos") or 0)>0 and name not in models:models.append(name)
    return models

def gemini_prompt(signal,history):
    return f"""
Voce e o AUDITOR GEMINI de sinais XAUUSD. Analise livremente o contexto.
O motor matematico e uma fonte de informacao, nao uma ordem.

Nao siga lista fixa de padroes ou categorias. Nao exija alinhamento perfeito
M5/M15/H1. Divergencia entre tempos graficos nao gera penalidade automatica.
Nao use pesos fixos nem soma obrigatoria. Identifique estruturas novas
sustentadas pelos dados. Pode discordar do motor matematico e explicar.
Avalie oportunidade, contexto, risco e incerteza de forma integrada.
Use somente os dados recebidos; nao invente cotacoes ou resultados.
Nao execute ordens.

A entrada operacional e fixa: use exatamente o ultimo fechamento M5
informado em preco_fechamento. Nao escolha nem altere a entrada.
Sua responsabilidade e calcular livremente stop_loss e take_profit.
Nao existe limite fixo de distancia do stop. Nao imponha ATR, RR ou
qualquer multiplicador fixo para stop ou alvo.
LONG: stop_loss < entrada < take_profit.
SHORT: take_profit < entrada < stop_loss.
Retorne JSON valido com classificacao APPROVED ou REJECTED, nota_auditoria
entre 0 e 100, justificativa, relatorio, stop_loss e take_profit.

Exemplo de formato:
{{
 "classificacao":"APPROVED ou REJECTED",
 "nota_auditoria":0,
 "entrada":4021.50,
 "stop_loss":4028.00,
 "take_profit":4011.75,
 "justificativa":"Motivo contextualizado",
 "pontuacao":{{}},
 "relatorio":"Analise completa"
}}

SINAL:
{json.dumps(signal,ensure_ascii=False,default=str)}

HISTORICO:
{json.dumps(history,ensure_ascii=False,default=str)}
"""
def call_gemini(model,signal,history):
    if not GEMINI:raise RuntimeError("Secret GEMINI_API_KEY ausente.")
    body={"contents":[{"parts":[{"text":gemini_prompt(signal,history)}]}],"generationConfig":{"temperature":.35,"responseMimeType":"application/json","maxOutputTokens":1800}}
    req=urllib.request.Request(API+"/"+model+":generateContent",data=json.dumps(body,ensure_ascii=False).encode(),headers={"Content-Type":"application/json","x-goog-api-key":GEMINI},method="POST")
    try:
        with urllib.request.urlopen(req,timeout=60) as response:raw=json.loads(response.read().decode())
    except urllib.error.HTTPError as e:raise RuntimeError(f"Gemini HTTP {e.code}: "+e.read().decode("utf-8","replace")[:800])
    parts=raw.get("candidates",[{}])[0].get("content",{}).get("parts",[])
    text="".join(p.get("text","") for p in parts).strip()
    if text.startswith("```"):text=text.replace("```json","",1).replace("```","").strip()
    try:r=json.loads(text)
    except Exception:raise RuntimeError("Resposta Gemini nao e JSON valido: "+text[:500])
    classification=str(r.get("classificacao","")).upper().strip()
    justification=str(r.get("justificativa","")).strip()
    rating=n(r.get("nota_auditoria")); entry=n(signal["preco_fechamento"]); stop=n(r.get("stop_loss")); target=n(r.get("take_profit"))
    if classification not in ("APPROVED","REJECTED") or not justification or rating is None or not 0<=rating<=100:raise RuntimeError("Classificacao, justificativa ou nota Gemini invalida.")
    if any(x is None or x<=0 for x in (entry,stop,target)):raise RuntimeError("Gemini nao forneceu fechamento M5, stop ou alvo validos.")
    if signal["direcao"]=="LONG" and not stop<entry<target:raise RuntimeError("Precos invalidos para LONG.")
    if signal["direcao"]=="SHORT" and not target<entry<stop:raise RuntimeError("Precos invalidos para SHORT.")
    report=str(r.get("relatorio","")).strip()
    if not report:raise RuntimeError("Gemini nao informou relatorio.")
    return {"classificacao":classification,"justificativa":justification[:3000],"nota":rating,"entrada":entry,"stop":stop,"alvo":target,"relatorio":report[:12000],"pontuacao":r.get("pontuacao",{}),"raw":raw}

def real_history(c):
    rows=[dict(x) for x in c.execute("SELECT codigo,classificacao_gemini,direcao,entrada,stop_loss,take_profit,resultado_usdt,status_resultado FROM ranking")]
    closed=[]; pending=0
    for x in rows:
        result=n(x.get("resultado_usdt"))
        if result is None or str(x.get("status_resultado") or "").upper()=="PENDENTE":pending+=1
        else:closed.append(x)
    stats={}
    for classification in ("APPROVED","REJECTED"):
        group=[x for x in closed if x["classificacao_gemini"]==classification]
        values=[n(x["resultado_usdt"]) for x in group]; wins=sum(1 for x in values if x>0)
        stats[classification]={"total_encerradas":len(values),"positivas":wins,"negativas":sum(1 for x in values if x<0),"resultado_medio_usdt":round(sum(values)/len(values),4) if values else None,"resultado_total_usdt":round(sum(values),4),"taxa_positiva_percentual":round(wins/len(values)*100,2) if values else None}
    ordered=sorted(closed,key=lambda x:n(x.get("resultado_usdt")) or 0,reverse=True)
    return {"disponibilidade":"DADOS_REAIS_CONSULTADOS","total_encerradas":len(closed),"total_pendentes":pending,"estatisticas":stats,"melhores_resultados":ordered[:10],"piores_resultados":ordered[-10:]}

def next_code(c):
    maximum=0
    for table in ("sinais_validados","sinais_negados","ranking"):
        for row in c.execute(f"SELECT codigo FROM {table}"):
            value=str(row[0] or "")
            if value.startswith("OP-") and value[3:].isdigit():maximum=max(maximum,int(value[3:]))
    return f"OP-{maximum+1:06d}"
def candle_done(c,timestamp):
    return c.execute("SELECT 1 FROM ranking WHERE data_hora=? LIMIT 1",(timestamp,)).fetchone() is not None

def persist(c,signal,result,model):
    package={"pipeline_integrado":True,"gemini":result["raw"],"classificacao_gemini":result.get("classificacao_gemini"),"nota_gemini":result.get("nota_gemini"),"score_tecnico":result.get("score_tecnico"),"score_combinado":result.get("score_combinado"),"relatorio":result["relatorio"],"pontuacao":result["pontuacao"],"telegram":{"status":"PENDENTE"}}
    response=json.dumps(package,ensure_ascii=False,default=str)
    c.execute("BEGIN IMMEDIATE")
    try:
        if result["classificacao"]=="APPROVED":
            c.execute("INSERT INTO sinais_validados (codigo,data_hora,direcao,entrada,stop_loss,take_profit,classificacao,justificativa,modelo,pontuacao,resposta_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",(signal["codigo"],signal["data_hora"],signal["direcao"],result["entrada"],result["stop"],result["alvo"],result["classificacao"],result["justificativa"],model,result["nota"],response))
        else:
            c.execute("INSERT INTO sinais_negados (codigo,data_hora,direcao,classificacao,justificativa,modelo,pontuacao,resposta_json) VALUES (?,?,?,?,?,?,?,?)",(signal["codigo"],signal["data_hora"],signal["direcao"],result["classificacao"],result["justificativa"],model,result["nota"],response))
        c.execute("INSERT INTO ranking (codigo,data_hora,classificacao_gemini,direcao,entrada,stop_loss,take_profit,pontuacao,resultado_usdt,status_resultado,atualizado_em) VALUES (?,?,?,?,?,?,?,?,NULL,'PENDENTE',?)",(signal["codigo"],signal["data_hora"],result.get("classificacao_gemini",result["classificacao"]),signal["direcao"],result["entrada"],result["stop"],result["alvo"],result["nota"],stamp()))
        c.commit()
    except Exception:c.rollback();raise

def telegram_send(signal,result,code):
    if not TG_TOKEN or not TG_CHAT:raise RuntimeError("Secrets TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID ausentes.")
    sent=now(); close=dt(signal["data_hora"]).replace(tzinfo=timezone.utc)+timedelta(minutes=5); delay=(sent-close).total_seconds()
    command=f"ENTREI {result['entrada']} {code}"
    text=chr(10).join(["XAUUSD - OURO","Timeframe: M5","",f"Codigo: {code}",f"Direcao: {signal['direcao']}",f"Entrada: {result['entrada']}",f"Stop: {result['stop']}",f"Alvo: {result['alvo']}",f"Nota Gemini: {result['nota']}","Gemini: APPROVED","",result["justificativa"],"",f"Fechamento candle UTC: {close.strftime('%Y-%m-%d %H:%M:%S UTC')}",f"Envio UTC: {sent.strftime('%Y-%m-%d %H:%M:%S UTC')}",f"Atraso: {delay:.1f} segundos","",f"Comando acompanhamento: {command}","Sinal informativo. Nenhuma ordem foi executada."])
    keyboard={"inline_keyboard":[[{"text":"Copiar comando","copy_text":{"text":command}}]]}
    data=urllib.parse.urlencode({"chat_id":TG_CHAT,"text":text[:4090],"disable_web_page_preview":"true","reply_markup":json.dumps(keyboard)}).encode()
    req=urllib.request.Request("https://api.telegram.org/bot"+TG_TOKEN+"/sendMessage",data=data,headers={"Content-Type":"application/x-www-form-urlencoded"},method="POST")
    try:
        with urllib.request.urlopen(req,timeout=30) as response:answer=json.loads(response.read().decode())
    except urllib.error.HTTPError as e:raise RuntimeError(f"Telegram HTTP {e.code}: "+e.read().decode("utf-8","replace")[:700])
    if not answer.get("ok"):raise RuntimeError("Telegram recusou mensagem: "+str(answer)[:700])
    return answer.get("result",{}).get("message_id")
def set_telegram_status(c,code,status,error=None,message_id=None):
    row=c.execute("SELECT resposta_json FROM sinais_validados WHERE codigo=?",(code,)).fetchone()
    if not row:return
    try:package=json.loads(row["resposta_json"] or "{}")
    except Exception:package={}
    package["telegram"]={"status":status,"erro":str(error)[:1000] if error else None,"message_id":str(message_id) if message_id is not None else None,"atualizado_em":stamp()}
    c.execute("UPDATE sinais_validados SET resposta_json=? WHERE codigo=?",(json.dumps(package,ensure_ascii=False,default=str),code));c.commit()
def send_pending(c):
    count=0
    rows=c.execute("SELECT codigo,data_hora,direcao,entrada,stop_loss,take_profit,pontuacao,justificativa,resposta_json FROM sinais_validados WHERE julianday(data_hora)>=julianday('now','-10 days') ORDER BY data_hora").fetchall()
    for x in rows:
        try:package=json.loads(x["resposta_json"] or "{}")
        except Exception:continue
        if not package.get("pipeline_integrado") or (package.get("telegram") or {}).get("status")=="ENVIADO":continue
        signal={"data_hora":x["data_hora"],"direcao":x["direcao"]}
        result={"entrada":x["entrada"],"stop":x["stop_loss"],"alvo":x["take_profit"],"nota":x["pontuacao"],"justificativa":x["justificativa"]}
        try:
            mid=telegram_send(signal,result,x["codigo"]);set_telegram_status(c,x["codigo"],"ENVIADO",message_id=mid);count+=1
        except Exception as error:
            set_telegram_status(c,x["codigo"],"PENDENTE",error=error);print(x["codigo"]+": Telegram pendente: "+str(error))
    return count
def retention(c):
    for table in ("sinais_validados","sinais_negados","ranking"):
        c.execute(f"DELETE FROM {table} WHERE julianday(data_hora)<julianday('now','-10 days')")
    c.commit()

def executar():
    c=db_open()
    try:
        readings=load_readings(c)
        latest=readings[-1]
        features=latest["features"]
        if len(features)<3:raise RuntimeError("Indicadores validos insuficientes para decisao final.")
        math_result=score(features)
        value=math_result["score_final"]
        if abs(value)<1e-12:
            print("Motor matematico sem direcao. Nenhum sinal emitido.");send_pending(c);retention(c);return
        direction="LONG" if value>0 else "SHORT"
        score_tecnico=min(100.0,abs(value)/max(1.0,len(features)*10.0)*100.0)
        print(json.dumps({"etapa":"motor_matematico","data_hora":latest["timestamp"],"preco_fechamento":latest["price"],"direcao":direction,"score_final":value,"score_tecnico":round(score_tecnico,4),"indicadores":math_result["indicadores"]},ensure_ascii=False,indent=2))
        if candle_done(c,latest["timestamp"]):
            print("Candle ja processado; sem duplicar.");send_pending(c);retention(c);return
        if not GEMINI:raise RuntimeError("Secret GEMINI_API_KEY ausente.")
        models=get_models();code=next_code(c)
        signal={"codigo":code,"data_hora":latest["timestamp"],"direcao":direction,"preco_fechamento":latest["price"],"score_final":value,"score_tecnico":score_tecnico,"peso_tecnico":PESO_TECNICO,"peso_gemini":PESO_GEMINI,"limite_aprovacao":LIMITE_APROVACAO,"nota_long":math_result["nota_long"],"nota_short":math_result["nota_short"],"janela_m5":WINDOW,"indicadores_utilizados":math_result["indicadores"],"indicadores_mtf":{tf:(dict(row) if row else None) for tf,row in latest["indicators"].items()},"tabuleiro":board_context(c)}
        history=real_history(c); attempts=models[:3]
        while len(attempts)<3:attempts.append(models[len(attempts)%len(models)])
        result=None;used=None;failures=[]
        for index,model in enumerate(attempts,1):
            try:
                print(f"Gemini tentativa {index}/3: {model}");result=call_gemini(model,signal,history);used=model;break
            except Exception as error:
                failures.append(model+": "+str(error));print("Falha Gemini:",error)
                if index<3:time.sleep(4)
        if result is None:raise RuntimeError("Tres tentativas Gemini falharam: "+" | ".join(failures))
        result["classificacao_gemini"]=result["classificacao"]
        result["nota_gemini"]=result["nota"]
        result["score_tecnico"]=score_tecnico
        result["score_combinado"]=round(score_tecnico*PESO_TECNICO+result["nota_gemini"]*PESO_GEMINI,4)
        result["nota"]=result["score_combinado"]
        result["classificacao"]="APPROVED" if result["score_combinado"]>=LIMITE_APROVACAO else "REJECTED"
        print(json.dumps({"etapa":"decisao_combinada","score_tecnico":score_tecnico,"nota_gemini":result["nota_gemini"],"pesos":{"tecnico":PESO_TECNICO,"gemini":PESO_GEMINI},"score_combinado":result["score_combinado"],"limite":LIMITE_APROVACAO,"classificacao_gemini":result["classificacao_gemini"],"classificacao_final":result["classificacao"]},ensure_ascii=False))
        persist(c,signal,result,used);print(f"{code}: {result['classificacao']} pelo score combinado.")
        send_pending(c);retention(c)
        check=c.execute("PRAGMA integrity_check").fetchone()[0]
        if check!="ok":raise RuntimeError("Falha de integridade SQLite: "+str(check))
        print("MOTOR INTEGRADO DE SINAIS CONCLUIDO.")
    finally:c.close()

if __name__=="__main__":executar()
