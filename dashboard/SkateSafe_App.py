import streamlit as st
import pandas as pd
import serial
import time

st.set_page_config(page_title="SkateSafe 監控系統", layout="wide")

# 強制網頁不要亂跳
st.markdown("<style>.main { overflow: hidden; }</style>", unsafe_allow_html=True)
st.title("🛹 SkateSafe: 實時監控 (穩定優化版)")

@st.cache_resource
def get_serial_connection():
    try:
        # 開啟時設定較短的 timeout 增加反應速度
        return serial.Serial('COM8', 115200, timeout=0.01)
    except:
        return None

ser = get_serial_connection()

col1, col2 = st.columns([1, 2])
with col1:
    st.subheader("設備狀態")
    status_placeholder = st.empty()
    metric_placeholder = st.empty()

with col2:
    st.subheader("G 力即時走勢")
    chart_placeholder = st.empty()

if 'history' not in st.session_state:
    st.session_state.history = []

if ser:
    status_placeholder.success("✅ 已連線 (COM8)")
    
    while True:
        # 1. 緩衝區管理：如果堆積太多資料，直接清空，只抓當下最準的一筆
        if ser.in_waiting > 100:
            ser.reset_input_buffer()
            
        if ser.in_waiting > 0:
            try:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                if line:
                    # 2. 數據清洗：確保這行資料是完整的數字
                    val = float(line.split(',')[0])
                    
                    # 3. 數據過濾：過小的雜訊不畫圖，節省效能
                    if abs(val) < 0.05: val = 0.0 

                    st.session_state.history.append(val)
                    if len(st.session_state.history) > 40:
                        st.session_state.history.pop(0)
                    
                    # 4. 更新 UI
                    metric_placeholder.metric("當前加速度", f"{val:.2f} G")
                    
                    # 每兩次讀取才更新一次圖表，減輕網頁負擔（這能大幅解決卡頓）
                    if len(st.session_state.history) % 2 == 0:
                        df = pd.DataFrame(st.session_state.history, columns=["G-Force"])
                        chart_placeholder.line_chart(df, height=350)
                    
            except:
                continue # 遇到亂碼直接跳過，不中斷程式
        
        time.sleep(0.01) # 給系統一點喘息空間
else:
    status_placeholder.error("❌ 找不到 COM8，請確認連線並重整")