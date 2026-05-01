🛹 SkateSafe: 滑板運動衝擊即時監控與醫療串接系統
📌 專案簡介
本專案專為滑板運動設計，透過 ESP32 硬體感測與 Streamlit 網頁技術，實現實時衝擊力（G-Force）監控。當偵測到可能導致腦震盪的高重力撞擊時，系統將引導使用者進行健康問卷，並將數據封裝為 FHIR 醫療標準格式上傳，建立運動傷害的數位追蹤機制。  

🏗️ 系統架構與檔案職責
1. 核心程式 (Core Logic)
dashboard/SkateSafe_App.py (系統主入口)：

負責全域頁面路由、使用者登入驗證、健康紀錄讀寫及 FHIR 資料組裝與上傳流程。  

dashboard/monitor_runtime.py (數據處理後台)：

獨立負責 Serial 連線維護、感測器 raw data 解析、平滑濾波、死區處理與 Impact 判定，並提供圖表輪詢端點。  

2. 前端視覺化 (Visual Components)
dashboard/components/live_monitor_chart/index.html：

採用 HTML5 Canvas 與 JavaScript 打造的高頻自定義圖表，解決 Streamlit 內建圖表的閃爍問題，提供平滑流暢的波形顯示。  

3. 嵌入式韌體 (Firmware)
firmware/saketsafe/saketsafe.ino：

燒錄於 ESP32 之 Arduino 程式，負責 MPU6050 初始化、量程設定（±16g），並透過序列埠輸出經換算的 G 值數據。  

4. 資料儲存與管理 (Data Management)
users.json：存儲帳號、雜湊密碼及對應的 Patient ID。  

user_health_logs.json：存儲主觀健康問卷回報、撞擊事件及雲端同步狀態。  

impact_log.json：輕量化撞擊紀錄檔，用於快速事件追蹤。  

auth_sessions.json：維護本地登入 Token，實現持續登入功能。  

🛡️ 安全與配置
.gitignore：嚴格過濾包含個人隱私的 .json 檔與快取檔案，避免敏感數據流向 GitHub。  

requirements.txt：定義專案所需的 streamlit, requests, pyserial, pandas, altair 等相依套件。  

🚀 快速啟動
韌體部署：將 firmware/ 下的程式碼燒錄至 ESP32。

環境安裝：pip install -r requirements.txt

執行系統：

Bash
streamlit run dashboard/SkateSafe_App.py
📊 開發歷程與技術特點
數據流優化：透過 monitor_runtime.py 實現後台數據處理，與前端 UI 解耦，確保高頻採樣下網頁不卡頓。  

穩定化渲染：導入自定義 Canvas 元件取代標準圖表，大幅提升 Demo 現場的視覺流動感。  

醫療標準化：實作 HL7 FHIR 協議，將運動撞擊數據轉化為具備互通性的醫療資源。  

💡 專題開發小結 (大安電子科)
本專案結合了硬體控制、軟硬整合通訊、Web 開發與雲端醫療標準。在開發過程中，我們解決了序列埠緩衝區堆積導致的延遲問題，並透過「極簡化重構」提升了系統在極端環境下的穩定度。