import hashlib
import json
import re
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
import serial
import streamlit as st


ROOT_DIR = Path(__file__).resolve().parent.parent
USERS_PATH = ROOT_DIR / "users.json"
IMPACT_LOG_PATH = ROOT_DIR / "impact_log.json"
USER_HEALTH_LOGS_PATH = ROOT_DIR / "user_health_logs.json"
FHIR_BASE_URL = "https://tzuchi-fhir.ddns.net/fhir"
DEFAULT_BG_COLOR = "#0e1117"
ALERT_BG_COLOR = "#4a0000"
FHIR_HEADERS = {
    "Accept": "application/fhir+json",
    "Content-Type": "application/fhir+json",
}


st.set_page_config(
    page_title="SkateSafe 專業監測儀表板",
    layout="wide",
)


def initialize_session_state():
    defaults = {
        "bg_color": DEFAULT_BG_COLOR,
        "offset": 0.0,
        "history": [],
        "smooth_val": 0.0,
        "authenticated": False,
        "user_email": "",
        "user_name": "",
        "patient_id": "",
        "patient_resource": None,
        "last_fhir_debug": None,
    }

    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def reset_monitoring_state():
    st.session_state.bg_color = DEFAULT_BG_COLOR
    st.session_state.offset = 0.0
    st.session_state.history = []
    st.session_state.smooth_val = 0.0


def clear_auth_state():
    st.session_state.authenticated = False
    st.session_state.user_email = ""
    st.session_state.user_name = ""
    st.session_state.patient_id = ""
    st.session_state.patient_resource = None
    st.session_state.last_fhir_debug = None
    reset_monitoring_state()


def load_users():
    if not USERS_PATH.exists():
        return {"users": []}

    try:
        users_payload = json.loads(USERS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{USERS_PATH.name} 的 JSON 格式無效。") from exc

    if not isinstance(users_payload, dict) or not isinstance(
        users_payload.get("users"), list
    ):
        raise ValueError(f"{USERS_PATH.name} 必須包含頂層的 'users' 陣列。")

    return users_payload


def save_users(users_payload):
    USERS_PATH.write_text(
        json.dumps(users_payload, indent=2),
        encoding="utf-8",
    )


def get_current_user_id():
    patient_id = st.session_state.get("patient_id", "")
    if "/" not in patient_id:
        return ""
    return patient_id.split("/", 1)[1]


def load_user_health_logs(show_error=True):
    if not USER_HEALTH_LOGS_PATH.exists():
        return []

    try:
        health_logs = json.loads(USER_HEALTH_LOGS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        if show_error:
            st.error("user_health_logs.json 格式無效，無法讀取健康紀錄。")
        return None

    if not isinstance(health_logs, list):
        if show_error:
            st.error("user_health_logs.json 必須是 JSON 陣列。")
        return None

    return health_logs


def save_user_health_logs(logs):
    USER_HEALTH_LOGS_PATH.write_text(
        json.dumps(logs, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def append_user_health_log(entry):
    health_logs = load_user_health_logs(show_error=False)
    if health_logs is None:
        raise ValueError("user_health_logs.json 格式無效，無法寫入健康紀錄。")

    health_logs.append(entry)
    save_user_health_logs(health_logs)


def get_recent_user_health_logs(user_id, limit=10):
    health_logs = load_user_health_logs(show_error=True)
    if health_logs is None:
        return []

    user_logs = [
        entry for entry in health_logs if entry.get("user_id") == user_id
    ]
    user_logs.sort(key=lambda entry: entry.get("timestamp", ""), reverse=True)
    return user_logs[:limit]


def normalize_email(email):
    return email.strip().lower()


def hash_password(password):
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def generate_patient_slug(email):
    normalized_email = normalize_email(email)
    slug = re.sub(r"[^a-z0-9]+", "-", normalized_email)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")

    if not slug:
        slug = "patient"

    if len(slug) > 64:
        digest = hashlib.sha256(normalized_email.encode("utf-8")).hexdigest()[:8]
        slug = f"{slug[:55].rstrip('-')}-{digest}"

    return slug


def generate_patient_reference(email):
    return f"Patient/{generate_patient_slug(email)}"


def find_user(users_payload, email):
    normalized_email = normalize_email(email)

    for user in users_payload["users"]:
        if normalize_email(user.get("email", "")) == normalized_email:
            return user

    return None


def build_patient_payload(name, email, patient_id):
    patient_slug = patient_id.split("/", 1)[1]
    return {
        "resourceType": "Patient",
        "id": patient_slug,
        "active": True,
        "identifier": [
            {
                "system": "http://skatesafe.local/identifier/email",
                "value": normalize_email(email),
            }
        ],
        "name": [{"text": name.strip()}],
        "managingOrganization": {"display": "SkateSafe_Clinic"},
    }


def validate_patient_payload(patient_payload, patient_slug):
    if not isinstance(patient_payload, dict) or not patient_payload:
        return "Patient payload 無效，無法送出空白請求。"

    if patient_payload.get("resourceType") != "Patient":
        return "Patient payload 缺少正確的 resourceType。"

    if patient_payload.get("id") != patient_slug:
        return f"Patient payload 的 id 與網址不一致：{patient_slug}"

    return None


def clear_fhir_debug():
    st.session_state.last_fhir_debug = None


def set_fhir_debug(debug_info):
    st.session_state.last_fhir_debug = debug_info


def summarize_response_text(response_text, limit=220):
    compact_text = re.sub(r"\s+", " ", response_text).strip()
    if not compact_text:
        return ""
    if len(compact_text) <= limit:
        return compact_text
    return f"{compact_text[:limit].rstrip()}..."


def format_request_exception(prefix, exc, include_body=False):
    response = getattr(exc, "response", None)

    if response is None:
        return f"{prefix}：{exc}"

    message = f"{prefix}（HTTP {response.status_code}）"

    reason = getattr(response, "reason", "")
    if reason:
        message = f"{message} {reason}"

    if include_body:
        body_preview = summarize_response_text(response.text)
        if body_preview:
            message = f"{message}。伺服器回應：{body_preview}"

    return message


def build_fhir_request_headers():
    return {
        "Accept": "application/fhir+json",
        "Content-Type": "application/fhir+json",
        "Prefer": "return=representation",
    }


def build_prepared_body_diagnostics(prepared_body):
    if prepared_body is None:
        return {
            "prepared_body_present": False,
            "prepared_body_length": 0,
            "prepared_body_preview": "",
        }

    if isinstance(prepared_body, bytes):
        prepared_body_text = prepared_body.decode("utf-8", errors="replace")
        prepared_body_length = len(prepared_body)
    else:
        prepared_body_text = str(prepared_body)
        prepared_body_length = len(prepared_body_text.encode("utf-8"))

    return {
        "prepared_body_present": prepared_body_length > 0,
        "prepared_body_length": prepared_body_length,
        "prepared_body_preview": summarize_response_text(prepared_body_text, limit=200),
    }


def render_fhir_debug():
    debug_info = st.session_state.get("last_fhir_debug")
    if not debug_info:
        return

    with st.expander("FHIR 診斷資訊"):
        st.json(debug_info)


def fetch_patient_resource(patient_id):
    patient_url = f"{FHIR_BASE_URL.rstrip('/')}/{patient_id}"

    try:
        response = requests.get(
            patient_url,
            headers={"Accept": FHIR_HEADERS["Accept"]},
            timeout=10,
        )
        response.raise_for_status()
        return response.json(), None
    except requests.RequestException as exc:
        return None, format_request_exception("無法從 FHIR 伺服器取得病患資料", exc)
    except ValueError:
        return None, "FHIR 伺服器回傳的病患資料不是有效的 JSON。"


def create_remote_patient(name, email, patient_id):
    patient_payload = build_patient_payload(name, email, patient_id)
    patient_slug = patient_id.split("/", 1)[1]
    patient_url = f"{FHIR_BASE_URL.rstrip('/')}/Patient/{patient_slug}"
    payload_error = validate_patient_payload(patient_payload, patient_slug)
    headers = build_fhir_request_headers()
    payload_json = json.dumps(patient_payload, ensure_ascii=False, separators=(",", ":"))
    payload_preview = summarize_response_text(payload_json, limit=200)
    debug_info = {
        "patient_id": patient_id,
        "patient_slug": patient_slug,
        "patient_url": patient_url,
        "payload_json_length": len(payload_json),
        "payload_preview": payload_preview,
        "expected_content_type": headers["Content-Type"],
    }

    if payload_error:
        set_fhir_debug(debug_info)
        return None, payload_error

    if not payload_json.strip():
        set_fhir_debug(debug_info)
        return None, "Patient payload 序列化後為空，已取消送出請求。"

    try:
        with requests.Session() as session:
            request = requests.Request(
                "PUT",
                patient_url,
                json=patient_payload,
                headers=headers,
            )
            prepared = session.prepare_request(request)

            debug_info.update(
                {
                    "prepared_method": prepared.method,
                    "prepared_url": prepared.url,
                    "prepared_content_type": prepared.headers.get("Content-Type", ""),
                    "prepared_content_length": prepared.headers.get("Content-Length", ""),
                }
            )
            debug_info.update(build_prepared_body_diagnostics(prepared.body))

            if not debug_info["prepared_body_present"]:
                set_fhir_debug(debug_info)
                return None, "送出前檢查失敗：PreparedRequest 沒有 request body。"

            response = session.send(
                prepared,
                timeout=10,
                allow_redirects=False,
            )

        debug_info.update(
            {
                "response_status_code": response.status_code,
                "response_location": response.headers.get("Location", ""),
                "response_body_preview": summarize_response_text(response.text),
            }
        )

        if 300 <= response.status_code < 400:
            set_fhir_debug(debug_info)
            redirect_location = response.headers.get("Location", "未提供")
            return (
                None,
                f"FHIR 端點發生轉址（HTTP {response.status_code}），Location：{redirect_location}。"
                " 可能在轉址過程中遺失 PUT body。",
            )

        response.raise_for_status()

        if response.content:
            try:
                patient_resource = response.json()
            except ValueError:
                patient_resource = patient_payload
        else:
            patient_resource = patient_payload

        if isinstance(patient_resource, dict):
            patient_resource.setdefault("resourceType", "Patient")
            patient_resource.setdefault("id", patient_slug)

        clear_fhir_debug()
        return patient_resource, None
    except requests.RequestException as exc:
        set_fhir_debug(debug_info)
        error_message = format_request_exception(
            f"無法建立 Patient 資源（病患 id：{patient_slug}）",
            exc,
            include_body=True,
        )
        if debug_info.get("prepared_body_present"):
            error_message = (
                f"{error_message}。送出前已確認 request body 存在，"
                f"長度為 {debug_info['prepared_body_length']} bytes，"
                "問題較可能出在轉址、代理層或伺服器解析。"
            )
        return None, error_message


def start_authenticated_session(user_record, patient_resource):
    reset_monitoring_state()
    st.session_state.authenticated = True
    st.session_state.user_email = user_record["email"]
    st.session_state.user_name = user_record["name"]
    st.session_state.patient_id = user_record["patient_id"]
    st.session_state.patient_resource = patient_resource
    clear_fhir_debug()


def export_to_fhir(g_force, patient_id):
    return {
        "resourceType": "Observation",
        "status": "final",
        "code": {
            "coding": [
                {
                    "system": "http://loinc.org",
                    "code": "80493-0",
                    "display": "G-force impact",
                }
            ]
        },
        "subject": {"reference": patient_id},
        "effectiveDateTime": datetime.now().isoformat(),
        "valueQuantity": {"value": g_force, "unit": "g"},
    }


@st.cache_resource
def get_serial_connection():
    try:
        return serial.Serial("COM8", 115200, timeout=0.01)
    except serial.SerialException:
        return None


initialize_session_state()

st.markdown(
    f"""
    <style>
        .stApp {{
            background-color: {st.session_state.bg_color};
            transition: background-color 0.3s;
            overflow: hidden;
        }}
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("SkateSafe 專業監測儀表板")

try:
    users_payload = load_users()
except ValueError as exc:
    st.sidebar.error(str(exc))
    st.error("users.json 格式無效，無法啟動驗證流程。")
    st.stop()


with st.sidebar:
    st.header("臨床資料存取")

    if not st.session_state.authenticated:
        with st.form("login_form"):
            login_email = st.text_input("電子郵件")
            login_password = st.text_input("密碼", type="password")
            login_submitted = st.form_submit_button(
                "登入",
                use_container_width=True,
            )

        if login_submitted:
            clear_fhir_debug()
            normalized_email = normalize_email(login_email)
            matched_user = find_user(users_payload, normalized_email)
            expected_hash = hash_password(login_password)

            if not normalized_email or not login_password:
                st.error("請輸入電子郵件與密碼。")
            elif not matched_user or matched_user.get("password") != expected_hash:
                st.error("電子郵件或密碼錯誤。")
            else:
                patient_resource, error_message = fetch_patient_resource(
                    matched_user["patient_id"]
                )
                if error_message:
                    st.error(error_message)
                    render_fhir_debug()
                else:
                    start_authenticated_session(matched_user, patient_resource)
                    st.rerun()

        st.caption("若您尚未擁有臨床資料存取帳號，請先註冊。")

        with st.form("register_form"):
            register_name = st.text_input("姓名")
            register_email = st.text_input("註冊電子郵件")
            register_password = st.text_input("註冊密碼", type="password")
            register_submitted = st.form_submit_button(
                "註冊",
                use_container_width=True,
            )

        if register_submitted:
            clear_fhir_debug()
            normalized_email = normalize_email(register_email)
            display_name = register_name.strip()

            if not display_name or not normalized_email or not register_password:
                st.error("姓名、電子郵件與密碼皆為必填。")
            elif find_user(users_payload, normalized_email):
                st.error("此電子郵件已註冊。")
            else:
                patient_id = generate_patient_reference(normalized_email)
                patient_resource, error_message = create_remote_patient(
                    display_name,
                    normalized_email,
                    patient_id,
                )

                if error_message:
                    st.error(error_message)
                    render_fhir_debug()
                else:
                    new_user = {
                        "email": normalized_email,
                        "password": hash_password(register_password),
                        "name": display_name,
                        "patient_id": patient_id,
                    }
                    users_payload["users"].append(new_user)
                    save_users(users_payload)
                    start_authenticated_session(new_user, patient_resource)
                    st.rerun()
    else:
        st.success(f"歡迎回來，{st.session_state.user_name}")
        st.caption(f"病患代碼：{st.session_state.patient_id}")

        if st.button("登出", use_container_width=True):
            clear_auth_state()
            st.rerun()

        st.divider()
        st.header("監測控制")
        impact_threshold = st.slider("重擊警示閾值 (G)", 10.0, 150.0, 50.0)

        if st.button("零點校準", use_container_width=True):
            if st.session_state.history:
                recent_window = st.session_state.history[-10:]
                st.session_state.offset = sum(recent_window) / len(recent_window)
                st.success(f"校準完成，偏移值：{st.session_state.offset:.2f}")
            else:
                st.warning("請先累積幾筆感測資料後再校準。")


if not st.session_state.get("authenticated"):
    st.info("請先登入以查看臨床資料。")
    st.stop()


current_user_id = get_current_user_id()

st.caption(f"目前登入者：{st.session_state.user_name}")

with st.expander("📝 自主健康回報"):
    report_symptom = st.selectbox(
        "症狀",
        ["無", "頭痛", "暈眩", "噁心", "關節痠痛", "其他"],
    )
    report_note = st.text_area("詳細說明")

    if st.button("提交回報"):
        report_entry = {
            "timestamp": datetime.now().isoformat(),
            "user_id": current_user_id,
            "type": "subjective_report",
            "symptom": report_symptom,
            "note": report_note.strip(),
            "g_force": None,
        }

        try:
            append_user_health_log(report_entry)
            st.success("自主健康回報已儲存。")
        except (OSError, ValueError) as exc:
            st.error(f"無法儲存健康回報：{exc}")


st.subheader("📅 歷史健康紀錄")
recent_health_logs = get_recent_user_health_logs(current_user_id, limit=10)

if recent_health_logs:
    type_labels = {
        "subjective_report": "自主回報",
        "impact_event": "重擊事件",
    }
    history_rows = [
        {
            "時間": entry.get("timestamp", ""),
            "類型": type_labels.get(entry.get("type", ""), entry.get("type", "")),
            "症狀": entry.get("symptom", ""),
            "說明": entry.get("note", ""),
            "G 值": entry.get("g_force"),
        }
        for entry in recent_health_logs
    ]
    history_df = pd.DataFrame(history_rows)
    st.dataframe(history_df, use_container_width=True, hide_index=True)
else:
    st.info("尚無紀錄")

col1, col2 = st.columns([1, 2])

with col1:
    status_placeholder = st.empty()
    metric_placeholder = st.empty()

    if st.button("匯出最新 FHIR JSON"):
        if st.session_state.history:
            st.json(
                export_to_fhir(
                    st.session_state.history[-1],
                    st.session_state.patient_id,
                )
            )
        else:
            st.warning("目前尚無衝擊資料可匯出。")

with col2:
    chart_placeholder = st.empty()


ser = get_serial_connection()

if ser:
    status_placeholder.success("COM8 感測器已連線")

    while True:
        if ser.in_waiting > 100:
            ser.reset_input_buffer()

        if ser.in_waiting > 0:
            try:
                line = ser.readline().decode("utf-8", errors="ignore").strip()
                if not line:
                    time.sleep(0.01)
                    continue

                raw_val = float(line.split(",")[0])

                if abs(raw_val) > 150.0:
                    continue

                if st.session_state.history:
                    diff = abs(raw_val - st.session_state.smooth_val)
                    if diff > 80.0:
                        continue

                st.session_state.smooth_val = (
                    raw_val * 0.15
                ) + (st.session_state.smooth_val * 0.85)

                val = st.session_state.smooth_val - st.session_state.offset
                if abs(val) < 0.15:
                    val = 0.0

                st.session_state.history.append(val)
                if len(st.session_state.history) > 40:
                    st.session_state.history.pop(0)

                if val >= impact_threshold:
                    st.session_state.bg_color = ALERT_BG_COLOR
                    log_entry = {
                        "time": datetime.now().strftime("%H:%M:%S"),
                        "g": val,
                    }
                    with IMPACT_LOG_PATH.open("a", encoding="utf-8") as log_file:
                        log_file.write(json.dumps(log_entry) + "\n")
                    impact_event_entry = {
                        "timestamp": datetime.now().isoformat(),
                        "user_id": current_user_id,
                        "type": "impact_event",
                        "symptom": "",
                        "note": "偵測到重擊事件",
                        "g_force": val,
                    }
                    try:
                        append_user_health_log(impact_event_entry)
                    except (OSError, ValueError):
                        pass
                else:
                    st.session_state.bg_color = DEFAULT_BG_COLOR

                metric_placeholder.metric("目前衝擊值", f"{val:.2f} G")

                if len(st.session_state.history) % 2 == 0:
                    history_df = pd.DataFrame(
                        st.session_state.history,
                        columns=["G-Force"],
                    )
                    chart_placeholder.line_chart(history_df, height=350)
            except (ValueError, IndexError, serial.SerialException):
                continue

        time.sleep(0.01)
else:
    status_placeholder.error("無法連線至 COM8")
