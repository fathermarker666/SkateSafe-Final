# 🛹 SkateSafe: 滑板運動衝擊即時監控系統 (決賽版本)

## 📌 專案簡介
本專案專為滑板運動設計，透過 ESP32 與 MPU6050 感測器，實時監測運動員受到的衝擊力（G-Force）。
系統旨在提供醫護端即時數據，當衝擊力超過安全閾值（例如 80G）時發出預警，作為腦震盪風險評估的依據。

## 🛠️ 技術關鍵 (核心優化)
與一般開源範例不同，本版本針對實作中的硬體衝突與延遲進行了深度修復：
- **硬體通訊**：解決了 MPU6050 函式庫與 ESP32 I2C 腳位宣告的衝突，改用底層 `Wire` 協議直接存取暫存器。
- **低延遲優化**：實作了 Python Serial 緩衝區重置機制 (`reset_input_buffer`)，消除堆積數據導致的畫面延遲。
- **數據平滑**：透過 Streamlit 的 `st.empty` 原地刷新機制，解決網頁無限增長問題。

## 🚀 快速啟動
1. 將 `firmware/` 資料夾內的 `.ino` 檔案燒錄至 ESP32。
2. 確認設備連接至電腦 COM 埠（本專案預設為 COM8）。
3. 安裝必要套件：`pip install -r requirements.txt`
4. 啟動監控網頁：`python -m streamlit run dashboard/SkateSafe_App.py`

## 📊 開發歷程
- [x] 完成 I2C 底層通訊調校
- [x] 實現 Streamlit 實時圖表視覺化
- [ ] 串接 FHIR 醫療格式輸出 (開發中)