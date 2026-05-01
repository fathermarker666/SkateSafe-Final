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
        "is_skating": False,
        "health_form_expanded": False,
        "current_g_force": None,
        "page": "home",
        "show_auth_panel": False,
        "is_monitoring": False,
        "has_impact_occurred": False,
        "impact_flash_until": 0.0,
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
    close_serial_connection()
    st.session_state.authenticated = False
    st.session_state.user_email = ""
    st.session_state.user_name = ""
    st.session_state.patient_id = ""
    st.session_state.patient_resource = None
    st.session_state.last_fhir_debug = None
    st.session_state.is_skating = False
    st.session_state.health_form_expanded = False
    st.session_state.current_g_force = None
    st.session_state.page = "home"
    st.session_state.show_auth_panel = False
    st.session_state.is_monitoring = False
    st.session_state.has_impact_occurred = False
    st.session_state.impact_flash_until = 0.0
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


def format_boolean_answer(value):
    return "是" if value else "否"


def build_questionnaire_answers(
    is_skating,
    skating_hit,
    hit_note,
    pain_scale,
    old_injury,
    rehab_done,
    mental_scale,
):
    answers = [
        {
            "linkId": "Q0",
            "text": "Q0: 您現在是否正在進行/剛結束滑板運動？",
            "answer": format_boolean_answer(is_skating),
        }
    ]

    if is_skating:
        answers.append(
            {
                "linkId": "Q1A",
                "text": "Q1: 剛才是否有發生跌倒或任何部位撞擊？",
                "answer": format_boolean_answer(skating_hit),
            }
        )
        if skating_hit:
            answers.append(
                {
                    "linkId": "Q1A-sub",
                    "text": "Q1-sub: 請描述撞擊部位與感受",
                    "answer": hit_note.strip(),
                }
            )
        answers.append(
            {
                "linkId": "Q2A",
                "text": "Q2: 目前的疼痛量表 (1-10)",
                "answer": str(pain_scale),
            }
        )
    else:
        answers.append(
            {
                "linkId": "Q1B",
                "text": "Q1: 今日日常生活中是否有舊傷復發感？",
                "answer": format_boolean_answer(old_injury),
            }
        )
        answers.append(
            {
                "linkId": "Q2B",
                "text": "Q2: 今日是否有進行醫囑復健運動？",
                "answer": format_boolean_answer(rehab_done),
            }
        )
        answers.append(
            {
                "linkId": "Q3B",
                "text": "Q3: 目前的精神狀態評分 (1-10)",
                "answer": str(mental_scale),
            }
        )

    return answers


def build_questionnaire_summary(answers):
    summary_labels = {
        "Q0": "滑板狀態",
        "Q1A": "跌倒/撞擊",
        "Q1A-sub": "撞擊描述",
        "Q2A": "疼痛量表",
        "Q1B": "舊傷復發感",
        "Q2B": "復健運動",
        "Q3B": "精神狀態",
    }
    summary_parts = []

    for answer_entry in answers:
        link_id = answer_entry.get("linkId", "")
        label = summary_labels.get(link_id, answer_entry.get("text", link_id))
        answer_value = str(answer_entry.get("answer", "")).strip()
        if answer_value:
            summary_parts.append(f"{label}：{answer_value}")

    return "；".join(summary_parts)


def determine_questionnaire_symptom(is_skating, skating_hit):
    if is_skating and skating_hit:
        return "跌倒/撞擊後評估"
    if is_skating:
        return "滑板後自評"
    return "日常/復健自評"


def format_health_log_for_table(entry):
    type_labels = {
        "subjective_report": "自主回報",
        "impact_event": "重擊事件",
    }
    entry_type = entry.get("type", "")
    answers = entry.get("answers")

    if entry_type == "subjective_report" and isinstance(answers, list) and answers:
        summary = build_questionnaire_summary(answers)
    elif entry_type == "impact_event":
        summary = entry.get("note", "")
    else:
        legacy_parts = [
            part
            for part in [entry.get("symptom", ""), entry.get("note", "")]
            if str(part).strip()
        ]
        summary = "；".join(legacy_parts)

    return {
        "時間": entry.get("timestamp", ""),
        "類型": type_labels.get(entry_type, entry_type),
        "摘要": summary,
        "G 值": entry.get("g_force"),
    }


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


def close_serial_connection():
    ser = get_serial_connection()
    if ser:
        try:
            if ser.is_open:
                ser.close()
        except serial.SerialException:
            pass
    get_serial_connection.clear()


def start_monitoring_session():
    reset_monitoring_state()
    st.session_state.bg_color = DEFAULT_BG_COLOR
    st.session_state.is_monitoring = True
    st.session_state.has_impact_occurred = False
    st.session_state.impact_flash_until = 0.0
    st.session_state.current_g_force = None
    st.session_state.health_form_expanded = False
    st.session_state.page = "monitor"
    st.session_state.chart_obj = None


def stop_and_finalize_monitoring():
    st.session_state.is_monitoring = False
    st.session_state.bg_color = DEFAULT_BG_COLOR
    st.session_state.impact_flash_until = 0.0
    close_serial_connection()
    st.session_state.chart_obj = None
    if "chart_container" in st.session_state:
        del st.session_state.chart_container

    if st.session_state.has_impact_occurred:
        st.session_state.is_skating = True
        st.session_state.health_form_expanded = True
        st.session_state.page = "questionnaire"
    else:
        st.session_state.page = "home"


def handle_detected_impact(current_user_id, g_force):
    st.session_state.has_impact_occurred = True
    st.session_state.is_skating = True
    st.session_state.current_g_force = g_force
    st.session_state.bg_color = ALERT_BG_COLOR
    st.session_state.impact_flash_until = time.time() + 0.2

    log_entry = {
        "time": datetime.now().strftime("%H:%M:%S"),
        "g": g_force,
    }
    with IMPACT_LOG_PATH.open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(log_entry) + "\n")

    impact_event_entry = {
        "timestamp": datetime.now().isoformat(),
        "user_id": current_user_id,
        "type": "impact_event",
        "symptom": "",
        "note": "偵測到重擊事件",
        "g_force": g_force,
    }
    try:
        append_user_health_log(impact_event_entry)
    except (OSError, ValueError):
        pass


def upload_fhir_resource(resource_type, resource_payload):
    resource_url = f"{FHIR_BASE_URL.rstrip('/')}/{resource_type}"

    try:
        response = requests.post(
            resource_url,
            json=resource_payload,
            headers=FHIR_HEADERS,
            timeout=10,
        )
        response.raise_for_status()
        if response.content:
            try:
                return response.json(), None
            except ValueError:
                return resource_payload, None
        return resource_payload, None
    except requests.RequestException as exc:
        return None, format_request_exception(
            f"無法上傳 {resource_type} 資源",
            exc,
            include_body=True,
        )


def build_questionnaire_response_resource(entry, patient_id):
    answers = entry.get("answers")
    items = []

    if isinstance(answers, list) and answers:
        for answer in answers:
            answer_value = str(answer.get("answer", "")).strip()
            if not answer_value:
                continue
            items.append(
                {
                    "linkId": answer.get("linkId", ""),
                    "text": answer.get("text", ""),
                    "answer": [{"valueString": answer_value}],
                }
            )
    else:
        summary = " / ".join(
            [
                str(part).strip()
                for part in [entry.get("symptom", ""), entry.get("note", "")]
                if str(part).strip()
            ]
        )
        if summary:
            items.append(
                {
                    "linkId": "legacy-summary",
                    "text": "自主健康回報摘要",
                    "answer": [{"valueString": summary}],
                }
            )

    return {
        "resourceType": "QuestionnaireResponse",
        "status": "completed",
        "subject": {"reference": patient_id},
        "authored": entry.get("timestamp", datetime.now().isoformat()),
        "item": items,
    }


def build_log_upload_resources(entry, patient_id):
    entry_type = entry.get("type")

    if entry_type == "impact_event":
        observation = export_to_fhir(entry.get("g_force", 0.0), patient_id)
        observation["effectiveDateTime"] = entry.get(
            "timestamp",
            datetime.now().isoformat(),
        )
        note_text = str(entry.get("note", "")).strip()
        if note_text:
            observation["note"] = [{"text": note_text}]
        return [("Observation", observation)]

    if entry_type == "subjective_report":
        return [
            (
                "QuestionnaireResponse",
                build_questionnaire_response_resource(entry, patient_id),
            )
        ]

    return []


def upload_user_health_logs_to_fhir(user_id, patient_id):
    health_logs = load_user_health_logs(show_error=True)
    if health_logs is None:
        return 0, "無法讀取 user_health_logs.json。"

    user_logs = [entry for entry in health_logs if entry.get("user_id") == user_id]
    if not user_logs:
        return 0, "目前沒有可上傳的本地健康紀錄。"

    uploaded_count = 0
    error_messages = []

    for entry in user_logs:
        resources = build_log_upload_resources(entry, patient_id)
        if not resources:
            continue

        for resource_type, payload in resources:
            _, error_message = upload_fhir_resource(resource_type, payload)
            if error_message:
                error_messages.append(error_message)
            else:
                uploaded_count += 1

    if error_messages:
        combined_errors = "；".join(error_messages[:2])
        return uploaded_count, f"部分上傳失敗：{combined_errors}"

    return uploaded_count, None


def render_auth_panel(users_payload):
    st.subheader("帳號驗證")
    login_tab, register_tab = st.tabs(["登入", "註冊"])

    with login_tab:
        with st.form("login_form_main"):
            login_email = st.text_input("電子郵件")
            login_password = st.text_input("密碼", type="password")
            login_submitted = st.form_submit_button("登入", use_container_width=True)

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
                    st.session_state.show_auth_panel = False
                    st.rerun()

    with register_tab:
        with st.form("register_form_main"):
            register_name = st.text_input("姓名")
            register_email = st.text_input("註冊電子郵件")
            register_password = st.text_input("註冊密碼", type="password")
            register_submitted = st.form_submit_button("註冊", use_container_width=True)

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
                    st.session_state.show_auth_panel = False
                    st.rerun()


def render_questionnaire_section(current_user_id):
    st.subheader("📝 健康回報")

    if st.session_state.current_g_force is not None:
        st.warning(
            f"系統剛偵測到高衝擊事件（{st.session_state.current_g_force:.2f} G），"
            "請完成以下健康問卷。"
        )

    q0_default_index = 0 if st.session_state.is_skating else 1
    q0_is_skating = st.radio(
        "Q0: 您現在是否正在進行/剛結束滑板運動？",
        ["是", "否"],
        index=q0_default_index,
        key="q0_is_skating",
        horizontal=True,
    )
    questionnaire_is_skating = q0_is_skating == "是"

    skating_hit = False
    hit_note = ""
    pain_scale = 1
    old_injury = False
    rehab_done = False
    mental_scale = 1

    if questionnaire_is_skating:
        skating_hit = (
            st.radio(
                "Q1: 剛才是否有發生跌倒或任何部位撞擊？",
                ["是", "否"],
                key="q1_fall_or_hit",
                horizontal=True,
            )
            == "是"
        )
        if skating_hit:
            hit_note = st.text_area(
                "Q1-sub: 請描述撞擊部位與感受（如：頭部暈眩、關節劇痛）",
                key="q1_hit_note",
            )
        pain_scale = st.slider(
            "Q2: 目前的疼痛量表 (1-10)",
            1,
            10,
            3,
            key="q2_pain_scale",
        )
    else:
        old_injury = (
            st.radio(
                "Q1: 今日日常生活中是否有舊傷復發感？",
                ["是", "否"],
                key="q1_old_injury",
                horizontal=True,
            )
            == "是"
        )
        rehab_done = st.checkbox(
            "Q2: 今日是否有進行醫囑復健運動？",
            key="q2_rehab_done",
        )
        mental_scale = st.slider(
            "Q3: 目前的精神狀態評分 (1-10)",
            1,
            10,
            5,
            key="q3_mental_scale",
        )

    with st.form("structured_health_questionnaire_form"):
        submit_questionnaire = st.form_submit_button(
            "提交到本地 PHR",
            use_container_width=True,
        )

    if submit_questionnaire:
        if questionnaire_is_skating and skating_hit and not hit_note.strip():
            st.error("請補充撞擊部位與感受描述後再提交。")
            return

        answers = build_questionnaire_answers(
            questionnaire_is_skating,
            skating_hit,
            hit_note,
            pain_scale,
            old_injury,
            rehab_done,
            mental_scale,
        )
        summary_note = build_questionnaire_summary(answers)
        report_entry = {
            "timestamp": datetime.now().isoformat(),
            "user_id": current_user_id,
            "type": "subjective_report",
            "symptom": determine_questionnaire_symptom(
                questionnaire_is_skating,
                skating_hit,
            ),
            "note": summary_note,
            "g_force": st.session_state.current_g_force,
            "answers": answers,
        }

        try:
            append_user_health_log(report_entry)
            st.session_state.is_skating = questionnaire_is_skating
            st.session_state.health_form_expanded = False
            st.session_state.current_g_force = None
            st.success("結構化健康問卷已儲存到本地 PHR。")
            st.rerun()
        except (OSError, ValueError) as exc:
            st.error(f"無法儲存健康問卷：{exc}")


def render_history_section(current_user_id):
    st.subheader("📅 歷史紀錄")
    recent_health_logs = get_recent_user_health_logs(current_user_id, limit=10)

    if recent_health_logs:
        history_rows = [format_health_log_for_table(entry) for entry in recent_health_logs]
        history_df = pd.DataFrame(history_rows)
        st.dataframe(history_df, use_container_width=True, hide_index=True)
    else:
        st.info("尚無紀錄。")


def render_monitor_section(current_user_id):
    st.subheader("🚀 開始監測")
    control_col, chart_col = st.columns([1, 2])

    with control_col:
        impact_threshold = st.slider(
            "重擊警示閾值 (G)",
            10.0,
            150.0,
            50.0,
            key="impact_threshold",
        )

        if st.button("零點校準", use_container_width=True):
            if st.session_state.history:
                recent_window = st.session_state.history[-10:]
                st.session_state.offset = sum(recent_window) / len(recent_window)
                st.success(f"校準完成，偏移值：{st.session_state.offset:.2f}")
            else:
                st.warning("請先累積幾筆感測資料後再校準。")

        if st.button("結束監測", use_container_width=True):
            stop_and_finalize_monitoring()
            st.rerun()

        if st.button("匯出最新 FHIR JSON", use_container_width=True):
            if st.session_state.history:
                st.json(
                    export_to_fhir(
                        st.session_state.history[-1],
                        st.session_state.patient_id,
                    )
                )
            else:
                st.warning("目前尚無衝擊資料可匯出。")

        status_placeholder = st.empty()
        metric_placeholder = st.empty()
        impact_placeholder = st.empty()

    with chart_col:
        # 關鍵：如果 session_state 中沒有容器，才建立它
        if "chart_container" not in st.session_state:
            st.session_state.chart_container = st.empty()
        
        # 這裡不需要重複執行 chart_placeholder = st.empty()
        # 直接使用 session_state 裡的容器

    if not st.session_state.is_monitoring:
        status_placeholder.info("監測尚未啟動。")
        if st.session_state.history:
            metric_placeholder.metric("目前衝擊值", f"{st.session_state.history[-1]:.2f} G")
            history_df = pd.DataFrame(st.session_state.history, columns=["G-Force"])
            chart_placeholder.line_chart(history_df, height=350)
        return

    if (
        st.session_state.impact_flash_until
        and time.time() >= st.session_state.impact_flash_until
    ):
        st.session_state.bg_color = DEFAULT_BG_COLOR
        st.session_state.impact_flash_until = 0.0

    ser = get_serial_connection()

    if not ser:
        status_placeholder.error("無法連線至 COM8")
        st.session_state.is_monitoring = False
        return

    status_placeholder.success("COM8 感測器已連線")

    if ser.in_waiting > 100:
        ser.reset_input_buffer()

    processed = 0
    latest_val = st.session_state.history[-1] if st.session_state.history else 0.0

    while ser.in_waiting > 0 and processed < 100:
        try:
            line = ser.readline().decode("utf-8", errors="ignore").strip()
            if not line:
                processed += 1
                continue

            raw_val = float(line.split(",")[0])

            if abs(raw_val) > 150.0:
                processed += 1
                continue

            if st.session_state.history:
                diff = abs(raw_val - st.session_state.smooth_val)
                if diff > 80.0:
                    processed += 1
                    continue

            st.session_state.smooth_val = (
                raw_val * 0.15
            ) + (st.session_state.smooth_val * 0.85)

            val = st.session_state.smooth_val - st.session_state.offset
            if abs(val) < 0.15:
                val = 0.0

            latest_val = val
            st.session_state.history.append(val)
            if len(st.session_state.history) > 40:
                st.session_state.history.pop(0)

            if val >= impact_threshold:
                handle_detected_impact(current_user_id, val)

            processed += 1
        except (ValueError, IndexError, serial.SerialException):
            processed += 1
            continue

    metric_placeholder.metric("目前衝擊值", f"{latest_val:.2f} G")

    if st.session_state.has_impact_occurred:
        impact_placeholder.warning("本次監測期間已偵測到至少一次重擊事件。")
    else:
        impact_placeholder.info("目前尚未偵測到重擊事件。")

    # --- 最終修正後的繪圖邏輯 (請確保刪除所有 標記) ---
    if st.session_state.history:
        # 取得本次循環新增的數據點
        recent_data = pd.DataFrame(st.session_state.history[-processed:], columns=["G-Force"])
        
        # 檢查圖表物件是否有效
        if "chart_obj" not in st.session_state or st.session_state.chart_obj is None:
            # 第一次：在持久化的容器中初始化圖表
            st.session_state.chart_obj = st.session_state.chart_container.line_chart(recent_data, height=350)
        else:
            try:
                # 僅追加數據
                st.session_state.chart_obj.add_rows(recent_data)
            except Exception:
                # 若失效則重新繪製
                st.session_state.chart_obj = st.session_state.chart_container.line_chart(recent_data, height=350)

    time.sleep(0.12)
    st.rerun()


initialize_session_state()

st.markdown(
    f"""
    <style>
        .stApp {{
            background-color: {st.session_state.bg_color};
            transition: background-color 0.3s;
        }}
        div[data-testid="stButton"] > button {{
            min-height: 88px;
            font-size: 1.05rem;
            font-weight: 700;
        }}
    </style>
    """,
    unsafe_allow_html=True,
)

try:
    users_payload = load_users()
except ValueError as exc:
    st.error(str(exc))
    st.error("users.json 格式無效，無法啟動驗證流程。")
    st.stop()

header_left, header_right = st.columns([4, 1])

with header_left:
    st.title("SkateSafe 專業監測儀表板")

with header_right:
    if st.session_state.authenticated:
        st.caption(f"👤 {st.session_state.patient_id}")
        if st.button("登出", use_container_width=True):
            clear_auth_state()
            st.rerun()
    else:
        st.caption("🔑 請先登入")
        if st.button("登入 / 註冊", use_container_width=True):
            st.session_state.show_auth_panel = not st.session_state.show_auth_panel
            st.rerun()

if st.session_state.show_auth_panel:
    render_auth_panel(users_payload)
    if st.session_state.get("last_fhir_debug"):
        render_fhir_debug()
elif not st.session_state.authenticated:
    st.info("請點選右上角「登入 / 註冊」以開啟驗證表單。")

st.divider()

is_locked = not st.session_state.authenticated
if is_locked:
    st.warning("⚠️ 請先完成右上角登入，方可啟動監測與紀錄功能")
else:
    st.caption(f"目前登入者：{st.session_state.user_name}")

dashboard_row_1 = st.columns(2)
dashboard_row_2 = st.columns(2)

with dashboard_row_1[0]:
    if st.button("🚀 開始監測", use_container_width=True, disabled=is_locked):
        start_monitoring_session()

with dashboard_row_1[1]:
    if st.button("📝 健康回報", use_container_width=True, disabled=is_locked):
        if st.session_state.is_monitoring:
            st.warning("請先結束監測，再前往健康回報。")
        else:
            st.session_state.page = "questionnaire"

with dashboard_row_2[0]:
    if st.button("📅 歷史紀錄", use_container_width=True, disabled=is_locked):
        if st.session_state.is_monitoring:
            st.warning("請先結束監測，再查看歷史紀錄。")
        else:
            st.session_state.page = "history"

with dashboard_row_2[1]:
    if st.button("☁️ 上傳雲端", use_container_width=True, disabled=is_locked):
        if st.session_state.is_monitoring:
            st.warning("請先結束監測，再執行雲端上傳。")
        else:
            current_user_id = get_current_user_id()
            uploaded_count, error_message = upload_user_health_logs_to_fhir(
                current_user_id,
                st.session_state.patient_id,
            )
            if error_message:
                st.error(error_message)
            else:
                st.success(f"已成功上傳 {uploaded_count} 筆本地健康紀錄到 FHIR 雲端。")

if st.session_state.authenticated:
    current_user_id = get_current_user_id()
    st.divider()

    if st.session_state.page == "monitor":
        render_monitor_section(current_user_id)
    elif st.session_state.page == "questionnaire":
        render_questionnaire_section(current_user_id)
    elif st.session_state.page == "history":
        render_history_section(current_user_id)
    else:
        st.info("請從上方主控台選擇功能。")
