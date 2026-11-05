import base64, io, json, os, sqlite3
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
import streamlit as st

DB = Path("trades.db")
LABELS = ["BUY", "SELL", "NO TRADE"]
def connect():
    db=sqlite3.connect(DB)
    db.execute("""CREATE TABLE IF NOT EXISTS predictions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL, pair TEXT NOT NULL, timeframe TEXT NOT NULL,
        expiry_minutes INTEGER NOT NULL, signal TEXT NOT NULL,
        confidence TEXT NOT NULL, reasoning TEXT NOT NULL,
        indicators TEXT NOT NULL, entry_price REAL, expiry_price REAL,
        outcome TEXT NOT NULL DEFAULT 'Pending', image_name TEXT
    )""")
    db.commit()
    return db

def interpret(image_bytes, api_key, model):
    from openai import OpenAI
    encoded=base64.b64encode(image_bytes).decode()
    instruction="""You are a cautious chart screenshot analyst, not a trading execution system.
Read ONLY what is visible in the screenshot. Never invent indicator values or market data.
The user studies 1-minute Pocket Option OTC screenshots with Bollinger Bands, RSI,
ADX and +DI/-DI. Analyze price action, momentum, possible support/resistance,
and conflicts. The screenshot may be incomplete, blurry or before candle close.
Select BUY, SELL or NO TRADE. Prefer NO TRADE when evidence is weak or conflicting.
Confidence must be Low, Medium or High; never express numeric probability.
Return ONLY a JSON object with keys:
pair (string), timeframe (string), signal (BUY|SELL|NO TRADE),
confidence (Low|Medium|High), reasoning (array of 2-5 short strings),
indicators (object with keys rsi, adx, plus_di, minus_di, bollinger, trend;
each value a short description or "Not readable"), entry_price
(number or null), warnings (array of strings).
A screenshot is not proof of a future outcome. Never claim certainty."""
    client=OpenAI(api_key=api_key)
    response=client.chat.completions.create(
        model=model, temperature=0,
        response_format={"type":"json_object"},
        messages=[
            {"role":"system","content":instruction},
            {"role":"user","content":[
                {"type":"text","text":"Analyze this screenshot as a hypothetical next 1-minute simulation."},
                {"type":"image_url","image_url":{"url":"data:image/jpeg;base64,"+encoded}}
            ]}
        ]
    )
    return json.loads(response.choices[0].message.content)

st.set_page_config(page_title="Pocket Chart Lab",page_icon="📈",layout="wide")
st.title("📈 Pocket Chart Lab")
st.caption("Screenshot-based trading simulations • Educational analysis, not financial advice")
db=connect()
tab1,tab2,tab3=st.tabs(["Analyze screenshot","Record outcomes","Performance"])
with st.sidebar:
    st.header("AI settings")
    api_key=st.text_input("OpenAI API key",type="password",
                          value=os.environ.get("OPENAI_API_KEY",""),
                          help="Used only for an AI request. Not saved to the database.")
    model=st.text_input("Vision-capable model",value="gpt-4o-mini")
    st.info("Screenshot analysis requires an OpenAI API key and may incur API charges. Do not upload account credentials.")
with tab1:
    uploaded=st.file_uploader("Upload Pocket Option chart",type=["jpg","jpeg","png","webp"])
    expiry=st.number_input("Simulation expiry (minutes)",min_value=1,max_value=60,value=1)
    if uploaded:
        st.image(uploaded,caption="Screenshot to analyze",use_container_width=True)
        if st.button("Analyze screenshot",type="primary"):
            if not api_key:
                st.error("Enter an OpenAI API key in the sidebar.")
            else:
                try:
                    with st.spinner("Reading chart..."):
                        raw=uploaded.getvalue()
                        from PIL import Image
                        im=Image.open(io.BytesIO(raw)).convert("RGB")
                        im.thumbnail((1800,1800))
                        out=io.BytesIO(); im.save(out,format="JPEG",quality=85)
                        result=interpret(out.getvalue(),api_key,model)
                        st.session_state["latest"]=result
                        st.session_state["latest_file"]=uploaded.name
                except Exception as e:
                    st.error(f"Analysis failed: {e}")
    if "latest" in st.session_state:
        r=st.session_state["latest"]
        signal=r.get("signal","NO TRADE")
        if signal not in LABELS: signal="NO TRADE"
        st.subheader(f"Signal: {signal}")
        st.write(f"Confidence: {r.get('confidence','Low')}")
        for reason in r.get("reasoning",[]): st.write("• "+str(reason))
        st.json(r.get("indicators",{}))
        for warning in r.get("warnings",[]): st.warning(str(warning))
        st.caption("This is a hypothetical directional assessment, not a verified forecast.")
        pair=st.text_input("Pair (confirm/edit)",value=str(r.get("pair","Unknown")))
        timeframe=st.text_input("Timeframe (confirm/edit)",value=str(r.get("timeframe","M1")))
        entry=st.number_input("Entry price (optional; 0 = unknown)",min_value=0.0,
                              value=float(r.get("entry_price") or 0),format="%.6f")
        if st.button("Save simulation"):
            db.execute("""INSERT INTO predictions
              (created_at,pair,timeframe,expiry_minutes,signal,confidence,reasoning,indicators,entry_price,image_name)
              VALUES (?,?,?,?,?,?,?,?,?,?)""",
              (datetime.now(timezone.utc).isoformat(),pair,timeframe,int(expiry),signal,
               str(r.get("confidence","Low")),"; ".join(map(str,r.get("reasoning",[]))),
               json.dumps(r.get("indicators",{})),entry or None,st.session_state.get("latest_file")))
            db.commit()
            st.success("Saved. Record the actual outcome after expiry.")
with tab2:
    df=pd.read_sql_query("SELECT id,created_at,pair,signal,entry_price,expiry_price,outcome FROM predictions ORDER BY id DESC",db)
    if df.empty: st.info("No simulations saved yet.")
    else:
        st.dataframe(df,use_container_width=True,hide_index=True)
        selected=st.selectbox("Simulation ID to update",df["id"].tolist())
        row=df[df.id==selected].iloc[0]
        st.write(f"Recorded signal: **{row.signal}** | Entry: **{row.entry_price}**")
        exit_price=st.number_input("Price at actual expiration (0 = unknown)",min_value=0.0,
                                   value=float(row.expiry_price or 0),format="%.6f")
        result=st.selectbox("Verified result",["Pending","Win","Loss","Tie / Refund","Unknown"],
                            index=["Pending","Win","Loss","Tie / Refund","Unknown"].index(row.outcome))
        if st.button("Save verified result"):
            if result in ("Win","Loss") and exit_price and pd.notna(row.entry_price):
                derived=("Win" if (row.signal=="BUY" and exit_price>row.entry_price) or
                         (row.signal=="SELL" and exit_price<row.entry_price) else
                         "Tie / Refund" if exit_price==row.entry_price else "Loss")
                if row.signal=="NO TRADE": derived="Unknown"
                if derived!=result:
                    st.error(f"Entry/expiry prices imply '{derived}', not '{result}'. Check the prices.")
                    st.stop()
            db.execute("UPDATE predictions SET outcome=?,expiry_price=? WHERE id=?",
                       (result,exit_price or None,int(selected)))
            db.commit()
            st.success("Result updated.")
with tab3:
    df=pd.read_sql_query("SELECT * FROM predictions",db)
    if df.empty: st.info("Save simulations to view performance.")
    else:
        resolved=df[df.outcome.isin(["Win","Loss"])].copy()
        wins=int((resolved.outcome=="Win").sum()); losses=int((resolved.outcome=="Loss").sum())
        a,b,c=st.columns(3)
        a.metric("Verified wins",wins); b.metric("Verified losses",losses)
        c.metric("Win rate",f"{wins/(wins+losses):.1%}" if wins+losses else "N/A")
        st.caption("Pending, unknown and tied results are excluded. Small samples are unreliable.")
        if not resolved.empty:
            grouped=resolved.groupby(["pair","signal"]).outcome.agg(
                trades="count",wins=lambda s:(s=="Win").sum()).reset_index()
            grouped["win_rate"]=(grouped.wins/grouped.trades*100).round(1)
            st.dataframe(grouped,use_container_width=True,hide_index=True)
        payout=st.slider("Payout for break-even calculation",50,99,80)
        st.write(f"Break-even win rate at {payout}% net payout: **{100/(100+payout):.1%}** (before other costs).")
        st.download_button("Export all simulations (CSV)",df.to_csv(index=False),
                           file_name="simulations.csv",mime="text/csv")
db.close()
