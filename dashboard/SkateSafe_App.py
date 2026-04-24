import streamlit as st
import pandas as pd
import serial
import time
import json
from datetime import datetime

# 1. 頁面配置
st.set_page_config(page_title="SkateSafe 監控系統", layout="wide")

# 初始化 session_state 變數
if 'bg_color' not in st.session_state: st.session_state.bg_color = "#0e1117"
if 'offset' not in st.session_state: st.session_state.offset = 0.0
if 'history' not in st.session_state: st.session_state.history = []

st.markdown(f"""
    <style> .stApp {{ background-color: {st.session_state.bg_color}; transition: background-color 0.3s; overflow: hidden; }} </style>
    """, unsafe_allow_html=True)

st.title(" 🛹 SkateSafe: 醫療數據校準版")

if 'smooth_val' not in st.session_state:
    st.session_state.smooth_val = 0.0

# 2. 初始化 Serial
@st.cache_resource
def get_serial_connection():
    try:
        return serial.Serial('COM8', 115200, timeout=0.01)
    except:
        return None

ser = get_serial_connection()

# 3. 側邊欄控制
st.sidebar.header("系統控制面板")
impact_threshold = st.sidebar.slider("重擊預警閾值 (G)", 10.0, 150.0, 50.0)

# --- 新增：校準按鈕 ---
if st.sidebar.button("進行零點校準 (請保持平放)"):
    if st.session_state.history:
        # 取最近 10 筆的平均值作為偏差
        st.session_state.offset = sum(st.session_state.history[-10:]) / 10
        st.sidebar.success(f"校準完成！偏差值: {st.session_state.offset:.2f}")

def export_to_fhir(g_force):
    fhir_observation = {
        "resourceType": "Observation", "status": "final",
        "code": {"coding": [{"system": "http://loinc.org", "code": "80493-0", "display": "G-force impact"}]},
        "subject": {"reference": "Patient/skater-owen"},
        "effectiveDateTime": datetime.now().isoformat(),
        "valueQuantity": {"value": g_force, "unit": "g"}
    }
    return fhir_observation

# 4. 主佈局
col1, col2 = st.columns([1, 2])
with col1:
    status_placeholder = st.empty()
    metric_placeholder = st.empty()
    if st.button("🚀 產出 FHIR JSON"):
        if st.session_state.history:
            st.json(export_to_fhir(st.session_state.history[-1]))

with col2:
    chart_placeholder = st.empty()

# 5. 數據核心邏輯
if ser:
    status_placeholder.success("✅ 已連線 (COM8)")
    while True:
        if ser.in_waiting > 100: ser.reset_input_buffer()
            
        if ser.in_waiting > 0:
            try:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                if line:
                    raw_val = float(line.split(',')[0])
                    
                    # --- [ A. 異常尖峰過濾器 (Digital Fuse) ] ---
                    # 1. 絕對值過濾：超過 150G 的數據極大機率是通訊亂碼
                    if abs(raw_val) > 150.0:
                        continue
                    
                    # 2. 變化率過濾：如果這次與上次平滑值的差距大到誇張 (例如 > 80G)
                    # 且目前不是真的在高速運動，就視為突發雜訊剔除
                    if len(st.session_state.history) > 0:
                        diff = abs(raw_val - st.session_state.smooth_val)
                        if diff > 80.0:
                            continue

                    # --- [ B. 加權平滑濾波 ] ---
                    # 這裡將權重稍微調低 (0.15)，增加避震效果，防止 35G 這種跳動
                    st.session_state.smooth_val = (raw_val * 0.15) + (st.session_state.smooth_val * 0.85)
                    
                    # --- [ C. 校準與死區 ] ---
                    val = st.session_state.smooth_val - st.session_state.offset
                    if abs(val) < 0.15: val = 0.0  # 稍微提高死區，讓靜止圖表更像直線

                    # --- [ D. 紀錄與顯示 ] ---
                    st.session_state.history.append(val)
                    if len(st.session_state.history) > 40: 
                        st.session_state.history.pop(0)
                    
                    # 重擊判定與背景變色
                    if val >= impact_threshold:
                        st.session_state.bg_color = "#4a0000"
                        log_entry = {"time": datetime.now().strftime("%H:%M:%S"), "g": val}
                        with open("impact_log.json", "a") as f:
                            f.write(json.dumps(log_entry) + "\n")
                    else:
                        st.session_state.bg_color = "#0e1117"
                    
                    # 更新 UI 元件
                    metric_placeholder.metric("當前加速度", f"{val:.2f} G")
                    if len(st.session_state.history) % 2 == 0:
                        df = pd.DataFrame(st.session_state.history, columns=["G-Force"])
                        chart_placeholder.line_chart(df, height=350)
                        
            except Exception as e:
                # 這裡不噴錯，確保程式不中斷
                continue
        time.sleep(0.01)
else:
    status_placeholder.error("❌ 找不到 COM8")