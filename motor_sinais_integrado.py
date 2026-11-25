#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pipeline integrado de sinais XAUUSD. Usa somente as dez tabelas oficiais."""
import bisect, json, math, os, re, sqlite3, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parent
DB=ROOT/"data"/"xauusd.db"
MODEL=ROOT/"data"/"modelo_gemini.json"
TABLES={"m5","m15","h1","indicadores","tabuleiro","sinais_validados","sinais_negados","ranking","estatisticas_regioes","estatisticas_transicoes"}
TF={"M5":5,"M15":15,"H1":60}
WINDOW=48
HORIZON=3
PESO_TECNICO=0.50
PESO_GEMINI=0.50
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
    if names!=TABLES: raise RuntimeError("Banco fora do esquema: sao exigidas exatamente as dez tabelas oficiais. Encontradas: "+str(sorted(names)))
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
    max_points=max(1.0,len(detail)*10.0)
    return {
        "nota_long":round(buy,4),
        "nota_short":round(sell,4),
        "percentual_long":round(min(100.0,buy/max_points*100.0),4),
        "percentual_short":round(min(100.0,sell/max_points*100.0),4),
        "pontos_maximos":round(max_points,4),
        "indicadores":detail
    }

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
    for row in c.execute("SELECT * FROM tabuleiro ORDER BY data_hora DESC LIMIT 60"):
        x=dict(row)
        if x.get("sequencia_json"):
            try:x["sequencia_json"]=json.loads(x["sequencia_json"])
            except Exception:pass
        out.append(x)
    return out

def statistical_context(c):
    regions=[dict(x) for x in c.execute("SELECT * FROM estatisticas_regioes WHERE timeframe='M5' ORDER BY regiao")]
    transitions=[dict(x) for x in c.execute("SELECT * FROM estatisticas_transicoes WHERE timeframe='M5' ORDER BY origem,destino")]
    return {"regioes":regions,"transicoes":transitions}

def market_context(c):
    context={}
    for table in ("m5","m15","h1"):
        context[table]=[dict(x) for x in c.execute(f"SELECT * FROM {table} ORDER BY data_hora DESC LIMIT 40")]
    context["indicadores"]={}
    for tf in ("M5","M15","H1"):
        context["indicadores"][tf]=[dict(x) for x in c.execute(
            "SELECT * FROM indicadores WHERE timeframe=? ORDER BY data DESC,hora DESC LIMIT 30",(tf,))]
    context["tabuleiro"]=board_context(c)
    context["estatisticas"]=statistical_context(c)
    return context

def get_models():
    if not MODEL.is_file():raise RuntimeError("Artifact data/modelo_gemini.json do Testador nao encontrado.")
    with open(MODEL,encoding="utf-8") as f:data=json.load(f)
    if str(data.get("status","")).lower() not in ("pronto","pronto_com_aviso_api"):
        raise RuntimeError("Testador Gemini nao esta pronto nesta execucao: "+str(data.get("status")))
    approved=data.get("modelos_aprovados_execucao")
    if not isinstance(approved,list) or not approved:
        raise RuntimeError("Nenhum modelo aprovado nesta execucao; ranking historico nao sera usado como substituto.")
    models=[]
    for item in approved:
        name=str(item or "").strip()
        if name.startswith("models/gemini-") and name not in models:models.append(name)
    chosen=str(data.get("modelo_escolhido") or "").strip()
    if chosen in models:models.remove(chosen);models.insert(0,chosen)
    if not models:raise RuntimeError("Lista de modelos aprovados nesta execucao invalida.")
    return models

def gemini_prompt(signal,history,market):
    return f"""
Voce e o GESTOR DE MERCADO XAUUSD. Sua funcao e analisar o mercado, combinar
sua avaliacao independente com o motor matematico, consultar o historico e
decidir se existe uma oportunidade valida. Voce nao executa ordens.

ANALISE DO MERCADO
Analise todos os dados recebidos das dez tabelas oficiais: M5, M15, H1,
indicadores, tabuleiro, sinais_validados, sinais_negados, ranking,
estatisticas_regioes e estatisticas_transicoes. Nao ignore informacoes
relevantes. Os dados de operacoes anteriores estao no bloco HISTORICO.
Nao exija alinhamento perfeito entre timeframes, nao use uma lista fixa de
padroes e nao aplique penalidades automaticas por divergencia. Interprete
livremente o contexto, identifique comportamentos e padroes sustentados
pelos dados e explique incertezas. Pode discordar do motor matematico.

MOTOR MATEMATICO
Os pontos e percentuais LONG e SHORT abaixo foram calculados pelo motor.
Trate-os como uma fonte independente, nao como uma direcao preselecionada:
{json.dumps(signal["motor_matematico"],ensure_ascii=False)}

SUA AVALIACAO INDEPENDENTE
Depois de analisar o mercado, atribua seu proprio percentual de forca LONG
e seu proprio percentual de forca SHORT, cada um de 0 a 100. Nao copie os
valores do motor. Avalie cada direcao separadamente; os dois percentuais
nao precisam somar 100.

FORMULA OBRIGATORIA — PESO IGUAL
Calcule voce mesmo, nesta mesma resposta, as duas medias:
MEDIA LONG = (percentual matematico LONG + percentual Gemini LONG) / 2
MEDIA SHORT = (percentual matematico SHORT + percentual Gemini SHORT) / 2
O peso e exatamente 50% para cada fonte. Mostre os valores e o resultado
numerico. Nao faca uma segunda chamada.

HISTORICO E APRENDIZADO
Consulte integralmente os registros recebidos de sinais_validados,
sinais_negados e ranking. Diferencie operacoes encerradas de pendentes.
Considere resultados positivos e negativos, LONG e SHORT separadamente,
quando houver dados suficientes. Use o historico para avaliar a qualidade
da oportunidade, sem inventar resultados, sem tratar pendencias como
encerradas e sem transformar coincidencias em regras absolutas.

DECISAO
O limite minimo de aprovacao e {signal["limite_aprovacao"]} por cento.
A decisao e determinada pela media 50/50, sem veto discricionario:
- Se somente LONG atingir o limite, aprove LONG.
- Se somente SHORT atingir o limite, aprove SHORT.
- Se ambas atingirem, aprove a direcao com a maior media.
- Se nenhuma atingir, rejeite com NONE.
Riscos e historico devem explicar a qualidade e as ressalvas, mas nunca
vetar uma direcao cuja media atingiu o limite. Nao altere as medias.

DOIS CENARIOS
Apresente um setup LONG e um setup SHORT. Para ambos, a entrada deve ser
exatamente o ultimo fechamento M5: {signal["preco_fechamento"]}.
Defina stop_loss e take_profit coerentes com a estrutura observada.
LONG: stop_loss < entrada < take_profit.
SHORT: take_profit < entrada < stop_loss.
Nao invente precos nem use multiplicadores obrigatorios de ATR ou RR.
Explique a justificativa e os riscos de cada cenario.

FORMATO DE RESPOSTA
Retorne somente JSON valido, sem markdown, com esta estrutura:
{{
 "status":"APPROVED ou REJECTED",
 "direcao":"LONG, SHORT ou NONE",
 "avaliacao_gemini":{{"long_percentual":0,"short_percentual":0}},
 "medias":{{"long":0,"short":0}},
 "cenarios":{{
   "long":{{"entrada":0,"stop_loss":0,"take_profit":0,"justificativa":"","riscos":""}},
   "short":{{"entrada":0,"stop_loss":0,"take_profit":0,"justificativa":"","riscos":""}}
 }},
 "justificativa_decisao":"",
 "analise_historica":"",
 "relatorio_mercado":""
}}
Informe status e direcao conforme a regra deterministica da media.
A decisao final sera validada e definida pelo codigo Python, nao por um veto
do Gemini. Os dois cenarios devem conter precos numericos validos. Se os
dados forem insuficientes para construir um cenario, nao invente: use
valores nulos; o codigo impedira a aprovacao se os precos forem invalidos.

SINAL E MOTOR:
{json.dumps(signal,ensure_ascii=False,default=str)}

DADOS DE MERCADO DAS TABELAS OFICIAIS:
{json.dumps(market,ensure_ascii=False,default=str)}

HISTORICO DAS TRES TABELAS DE RESULTADOS:
{json.dumps(history,ensure_ascii=False,default=str)}
"""

class GeminiHTTPError(RuntimeError):
    def __init__(self,code,detail,retry_seconds=None):
        self.code=code
        self.retry_seconds=retry_seconds
        super().__init__(f"Gemini HTTP {code}: {json.dumps(detail,ensure_ascii=False)[:800]}")

def call_gemini(model,signal,history,market):
    if not GEMINI:raise RuntimeError("Secret GEMINI_API_KEY ausente.")
    body={"contents":[{"parts":[{"text":gemini_prompt(signal,history,market)}]}],
          "generationConfig":{"temperature":.35,"responseMimeType":"application/json","maxOutputTokens":3000}}
    req=urllib.request.Request(API+"/"+model+":generateContent",data=json.dumps(body,ensure_ascii=False).encode(),
        headers={"Content-Type":"application/json","x-goog-api-key":GEMINI},method="POST")
    try:
        with urllib.request.urlopen(req,timeout=60) as response:raw=json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        raw_error=e.read().decode("utf-8","replace")
        try:detail=json.loads(raw_error)
        except Exception:detail={"message":raw_error[:700]}
        retry=None
        header=e.headers.get("Retry-After") if e.headers else None
        if header:
            try:retry=max(1,int(float(header)+0.999))
            except (TypeError,ValueError):pass
        if retry is None and isinstance(detail,dict):
            for item in detail.get("error",{}).get("details",[]):
                if item.get("@type","").endswith("RetryInfo"):
                    match=re.search(r"(\d+(?:\.\d+)?)s",str(item.get("retryDelay","")))
                    if match:retry=max(1,int(float(match.group(1))+0.999))
        raise GeminiHTTPError(e.code,detail,retry)
    parts=raw.get("candidates",[{}])[0].get("content",{}).get("parts",[])
    text="".join(p.get("text","") for p in parts).strip()
    if text.startswith("```"):text=text.replace("```json","",1).replace("```","").strip()
    try:r=json.loads(text)
    except Exception:raise RuntimeError("Resposta Gemini nao e JSON valido: "+text[:700])
    own=r.get("avaliacao_gemini") or {}; averages=r.get("medias") or {}; scenarios=r.get("cenarios") or {}
    math_long=n(signal["motor_matematico"]["long_percentual"])
    math_short=n(signal["motor_matematico"]["short_percentual"])
    gem_long=n(own.get("long_percentual")); gem_short=n(own.get("short_percentual"))
    avg_long=n(averages.get("long")); avg_short=n(averages.get("short"))
    values=(math_long,math_short,gem_long,gem_short,avg_long,avg_short)
    if any(x is None or not 0<=x<=100 for x in values):
        raise RuntimeError("Percentuais Gemini ou medias ausentes/invalidos.")
    expected_long=(math_long+gem_long)/2
    expected_short=(math_short+gem_short)/2
    if abs(avg_long-expected_long)>.15 or abs(avg_short-expected_short)>.15:
        raise RuntimeError("Gemini informou medias diferentes da formula 50/50.")
    parsed={}
    entry=n(signal["preco_fechamento"])
    for key in ("long","short"):
        item=scenarios.get(key) or {}
        stop=n(item.get("stop_loss")); target=n(item.get("take_profit")); scenario_entry=n(item.get("entrada"))
        if scenario_entry is None or stop is None or target is None or min(scenario_entry,stop,target)<=0:
            raise RuntimeError("Setup "+key.upper()+" sem entrada, stop ou alvo numerico valido.")
        if abs(scenario_entry-entry)>0.000001:
            raise RuntimeError("Entrada do cenario "+key.upper()+" difere do ultimo fechamento M5.")
        if key=="long" and not stop<entry<target:
            raise RuntimeError("Precos invalidos para setup LONG.")
        if key=="short" and not target<entry<stop:
            raise RuntimeError("Precos invalidos para setup SHORT.")
        parsed[key]={"entrada":entry,"stop":stop,"alvo":target,
                     "justificativa":str(item.get("justificativa","")).strip(),
                     "riscos":str(item.get("riscos","")).strip()}
    justification=str(r.get("justificativa_decisao","")).strip()
    historical=str(r.get("analise_historica","")).strip()
    report=str(r.get("relatorio_mercado","")).strip()
    if not justification or not historical or not report:
        raise RuntimeError("Gemini nao forneceu justificativa, analise historica e relatorio.")
    limit=float(signal["limite_aprovacao"])
    long_ok=avg_long>=limit
    short_ok=avg_short>=limit
    if long_ok and (not short_ok or avg_long>avg_short):
        status="APPROVED"; direction="LONG"
    elif short_ok and (not long_ok or avg_short>avg_long):
        status="APPROVED"; direction="SHORT"
    else:
        # Sem direcao acima do limite ou empate exato: nao forcar sinal.
        status="REJECTED"; direction="NONE"
    if status=="APPROVED":
        selected=parsed[direction.lower()]
    else:
        selected={"entrada":None,"stop":None,"alvo":None}
    raw_result={"resposta_gemini":r,"modelo":model}
    return {"classificacao":status,"direcao":direction,"nota":avg_long if direction=="LONG" else avg_short if direction=="SHORT" else max(avg_long,avg_short),
            "entrada":selected.get("entrada"),"stop":selected.get("stop"),"alvo":selected.get("alvo"),
            "cenarios":parsed,"avaliacao_gemini":{"long":gem_long,"short":gem_short},
            "medias":{"long":avg_long,"short":avg_short},"justificativa":justification[:3000],
            "analise_historica":historical[:6000],"relatorio":report[:12000],
            "pontuacao":{"motor_long":math_long,"motor_short":math_short,"gemini_long":gem_long,
                         "gemini_short":gem_short,"media_long":avg_long,"media_short":avg_short},
            "raw":raw_result}

def real_history(c):
    validated=[dict(x) for x in c.execute(
        "SELECT codigo,data_hora,direcao,entrada,stop_loss,take_profit,classificacao,justificativa,modelo,pontuacao FROM sinais_validados ORDER BY data_hora")]
    denied=[dict(x) for x in c.execute(
        "SELECT codigo,data_hora,direcao,classificacao,justificativa,modelo,pontuacao FROM sinais_negados ORDER BY data_hora")]
    ranking=[dict(x) for x in c.execute(
        "SELECT codigo,data_hora,classificacao_gemini,direcao,entrada,stop_loss,take_profit,pontuacao,resultado_usdt,status_resultado,atualizado_em FROM ranking ORDER BY data_hora")]
    closed=[]; pending=0
    for x in ranking:
        result=n(x.get("resultado_usdt"))
        if result is None or str(x.get("status_resultado") or "").upper()=="PENDENTE":pending+=1
        else:closed.append(x)
    stats={}
    for direction in ("LONG","SHORT"):
        group=[x for x in closed if str(x.get("direcao") or "").upper()==direction]
        values=[n(x.get("resultado_usdt")) for x in group]
        wins=sum(1 for x in values if x>0)
        stats[direction]={"encerradas":len(values),"positivas":wins,
            "negativas":sum(1 for x in values if x<0),
            "resultado_total_usdt":round(sum(values),4),
            "taxa_positiva_percentual":round(wins/len(values)*100,2) if values else None}
    return {"sinais_validados":validated,"sinais_negados":denied,"ranking":ranking,
            "total_validados":len(validated),"total_negados":len(denied),
            "total_encerradas":len(closed),"total_pendentes":pending,
            "estatisticas_por_direcao":stats}

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
    package={"pipeline_integrado":True,"gemini":result["raw"],
        "classificacao_gemini":result["classificacao"],"nota_gemini":result["nota"],
        "score_tecnico":None,"score_combinado":result["nota"],
        "pontuacao":result["pontuacao"],"cenarios":result["cenarios"],
        "medias":result["medias"],"analise_historica":result["analise_historica"],
        "relatorio":result["relatorio"],"telegram":{"status":"PENDENTE"}}
    response=json.dumps(package,ensure_ascii=False,default=str)
    c.execute("BEGIN IMMEDIATE")
    try:
        if result["classificacao"]=="APPROVED":
            c.execute("INSERT INTO sinais_validados (codigo,data_hora,direcao,entrada,stop_loss,take_profit,classificacao,justificativa,modelo,pontuacao,resposta_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (signal["codigo"],signal["data_hora"],result["direcao"],result["entrada"],result["stop"],result["alvo"],
                 result["classificacao"],result["justificativa"],model,result["nota"],response))
        else:
            c.execute("INSERT INTO sinais_negados (codigo,data_hora,direcao,classificacao,justificativa,modelo,pontuacao,resposta_json) VALUES (?,?,?,?,?,?,?,?)",
                (signal["codigo"],signal["data_hora"],"NONE",result["classificacao"],result["justificativa"],model,result["nota"],response))
        c.execute("INSERT INTO ranking (codigo,data_hora,classificacao_gemini,direcao,entrada,stop_loss,take_profit,pontuacao,resultado_usdt,status_resultado,atualizado_em) VALUES (?,?,?,?,?,?,?,?,NULL,'PENDENTE',?)",
            (signal["codigo"],signal["data_hora"],result["classificacao"],result["direcao"],result["entrada"],result["stop"],result["alvo"],result["nota"],stamp()))
        c.commit()
    except Exception:
        c.rollback()
        raise

def telegram_send(signal,result,code):
    if not TG_TOKEN or not TG_CHAT:raise RuntimeError("Secrets TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID ausentes.")
    sent=now(); close=dt(signal["data_hora"]).replace(tzinfo=timezone.utc)+timedelta(minutes=5); delay=(sent-close).total_seconds()
    command=f"ENTREI {result['entrada']:.2f} {code}"
    direction=result["direcao"]
    text=chr(10).join([
        "🥇 XAUUSD · OURO","⏱ Timeframe: M5","",f"🆔 Código: {code}","",
        "LONG" if direction=="LONG" else "SHORT",
        f"💰 Entrada: {result['entrada']:.2f}",
        f"🛑 Stop: {result['stop']:.2f}",
        f"🎯 Alvo: {result['alvo']:.2f}",
        f"📊 Pontuação: {result['nota']:.2f}%",
        "🧠 Gemini: APPROVED","",result["justificativa"],
        "","━━━━━━━━━━━━━━━━━━",
        f"🕯️ Fechamento do candle UTC: {close.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"📨 Envio UTC: {sent.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"⏳ Atraso: {delay:.1f} segundos ({delay/60:.2f} minutos)",
        "","📲 Comando para acompanhamento:",command,
        "","⚠️ Sinal informativo. Nenhuma ordem foi executada."
    ])
    keyboard={"inline_keyboard":[[{"text":"📋 Copiar comando","copy_text":{"text":command}}]]}
    data=urllib.parse.urlencode({"chat_id":TG_CHAT,"text":text[:4090],
        "disable_web_page_preview":"true","reply_markup":json.dumps(keyboard)}).encode()
    req=urllib.request.Request("https://api.telegram.org/bot"+TG_TOKEN+"/sendMessage",data=data,
        headers={"Content-Type":"application/x-www-form-urlencoded"},method="POST")
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
        result={"direcao":x["direcao"],"entrada":x["entrada"],"stop":x["stop_loss"],"alvo":x["take_profit"],"nota":x["pontuacao"],"justificativa":x["justificativa"]}
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
        print(json.dumps({"etapa":"motor_matematico","data_hora":latest["timestamp"],
            "preco_fechamento":latest["price"],"long_pontos":math_result["nota_long"],
            "short_pontos":math_result["nota_short"],"long_percentual":math_result["percentual_long"],
            "short_percentual":math_result["percentual_short"],"indicadores":math_result["indicadores"]},
            ensure_ascii=False,indent=2))
        if candle_done(c,latest["timestamp"]):
            print("Candle ja processado; sem duplicar.");send_pending(c);retention(c);return
        if not GEMINI:raise RuntimeError("Secret GEMINI_API_KEY ausente.")
        models=get_models();code=next_code(c)
        stats=statistical_context(c)
        motor={"long_pontos":math_result["nota_long"],"short_pontos":math_result["nota_short"],
            "long_percentual":math_result["percentual_long"],"short_percentual":math_result["percentual_short"],
            "pontos_maximos":math_result["pontos_maximos"],"indicadores":math_result["indicadores"]}
        signal={"codigo":code,"data_hora":latest["timestamp"],"preco_fechamento":latest["price"],
            "motor_matematico":motor,"limite_aprovacao":LIMITE_APROVACAO,
            "peso_motor":PESO_TECNICO,"peso_gemini":PESO_GEMINI,
            "janela_m5":WINDOW}
        market=market_context(c)
        history=real_history(c)
        attempts=models[:3]
        result=None;used=None;failures=[]
        for index,model in enumerate(attempts,1):
            transient=0
            while True:
                try:
                    print(f"Gemini Gestor de Mercado tentativa {index}/{len(attempts)}: {model}")
                    result=call_gemini(model,signal,history,market);used=model
                    break
                except GeminiHTTPError as error:
                    failures.append(model+": "+str(error));print("Falha Gemini:",error)
                    if error.code==429:
                        if transient>=2:
                            raise RuntimeError("Cota Gemini permaneceu esgotada apos duas esperas; interrompido sem sinal. "+str(error))
                        wait=min(120,max(1,error.retry_seconds or 45))
                        transient+=1
                        print(f"HTTP 429: aguardando {wait}s antes de repetir o mesmo modelo.")
                        time.sleep(wait)
                        continue
                    if error.code in (400,401,403):
                        raise RuntimeError("Erro global/autenticacao Gemini; interrompido sem tentar outros modelos. "+str(error))
                    break
                except Exception as error:
                    failures.append(model+": "+str(error));print("Falha Gemini:",error)
                    break
            if result is not None:break
        if result is None:raise RuntimeError("Nenhum modelo aprovado concluiu a analise Gemini: "+" | ".join(failures))
        print(json.dumps({"etapa":"decisao_gestor_mercado","codigo":code,
            "direcao":result["direcao"],"status":result["classificacao"],
            "percentuais_gemini":result["avaliacao_gemini"],"medias_50_50":result["medias"],
            "limite":LIMITE_APROVACAO},ensure_ascii=False))
        persist(c,signal,result,used)
        print(f"{code}: {result['classificacao']} pelo Gestor de Mercado.")
        send_pending(c);retention(c)
        check=c.execute("PRAGMA integrity_check").fetchone()[0]
        if check!="ok":raise RuntimeError("Falha de integridade SQLite: "+str(check))
        print("MOTOR INTEGRADO DE SINAIS CONCLUIDO.")
    finally:c.close()

if __name__=="__main__":executar()
