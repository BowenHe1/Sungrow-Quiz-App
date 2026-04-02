import streamlit as st
import pandas as pd
import os
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
import streamlit.components.v1 as components

# --- CONFIGURATION ---
QUESTIONS_FILE = "question_pool.xlsx"
RESULTS_FILE = "quiz_results.csv"
TARGET_POINTS = 100
QUIZ_DURATION_SECONDS = 45 * 60  # 45 minutes
OPTION_COLS = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']

# --- HELPER FUNCTIONS ---
def load_questions():
    if not os.path.exists(QUESTIONS_FILE):
        st.error(f"File not found: {QUESTIONS_FILE}")
        return pd.DataFrame()
        
    df = pd.read_excel(QUESTIONS_FILE)
    
    # NEW: Capture the 1-based Excel row index (index + 2 because 0-based + header)
    df['row_index'] = df.index + 2

    # Clean data: Ensure columns are strings and fill NaNs
    cols_to_clean = OPTION_COLS + ['Correct Answer']
    for col in cols_to_clean:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()
        else:
            # If column E/F/G/H doesn't exist in Excel, create it as empty
            df[col] = ""
            
    # Clean Type column
    if 'Type' in df.columns:
        df['Type'] = df['Type'].astype(str).str.strip().str.lower()
        
    return df

def select_random_questions(df, target):
    if df.empty:
        return [], 0
        
    shuffled = df.sample(frac=1).reset_index(drop=True)
    selected = []
    current_points = 0
    
    for _, row in shuffled.iterrows():
        # Try to add question if points fit
        if current_points + row['Points'] <= target:
            selected.append(row)
            current_points += row['Points']
        
        # Stop if we hit exact target
        if current_points == target:
            break
            
    return selected, current_points


def grade_and_submit(questions, user_answers):
    """Grade answers and save submission. Used by both manual submit and auto-submit."""
    score = 0
    details_log = {}
    for i, q in enumerate(questions):
        u_ans = user_answers.get(i)
        q_type = q['Type']
        points = q['Points']
        r_idx = q['row_index']
        options_map = {letter: q.get(letter, "") for letter in OPTION_COLS if str(q.get(letter, "")).strip() != ""}
        c_key_str = str(q['Correct Answer']).upper()
        c_keys = [x.strip() for x in c_key_str.split(',')]
        correct_texts = [options_map[k] for k in c_keys if k in options_map]
        is_correct = False
        if q_type == 'single':
            if str(u_ans) == str(correct_texts[0]) if correct_texts else False:
                is_correct = True
        elif q_type == 'multi':
            if sorted(u_ans or []) == sorted(correct_texts):
                is_correct = True
        elif q_type == 'order':
            if (u_ans or []) == correct_texts:
                is_correct = True
        elif q_type == 'text':
            is_correct = None
        if is_correct:
            score += points
        details_log[r_idx] = {"answer": u_ans, "correct": is_correct, "type": q_type.capitalize()}
    save_submission(st.session_state['candidate_info'], score, st.session_state['total_points'], details_log)

def save_submission(candidate_info, score, max_score, answers_log):
    # 1. Define the Scope
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
    ]
    
    # 2. Authenticate
    s_info = st.secrets["gcp_service_account"]
    credentials = Credentials.from_service_account_info(
        s_info,
        scopes=scopes
    )
    
    # 3. Authorize (RENAME 'client' -> 'gc')
    gc = gspread.authorize(credentials)
    
    # 4. Open the Sheet (Use 'gc' here)
    # Using open_by_key is safer/faster than opening by name
    try:
        sh = gc.open_by_key("18kGBJLPUu-VdQT4bRdME-X29kJjv7f5GDNKnAQ7dU2s")
        worksheet = sh.worksheet("PT1.0_CSP_Safety") # consistently gets the corresponding tab
    except Exception as e:
        st.error(f"Google Sheets Connection Error: {e}")
        st.stop()
    
    # 5. Prepare and Append Row
    row = [
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        candidate_info['Name'],
        candidate_info['Email'],
        candidate_info['Company'],
        candidate_info['Instructor'],
        score,
        max_score,
        "",  # placeholder for rich-text answers cell
    ]
    
    # 5. Append main row (A-G), leave column H empty for rich text
    worksheet.append_row(row)

    # 6. Build rich text for the answers cell (column H)
    #    Q-labels -> bold orange | wrong answer text -> bold red | correct=FALSE -> bold red
    FMT_ORANGE  = {"bold": True, "foregroundColor": {"red": 1.0, "green": 0.6, "blue": 0.0}}
    FMT_RED     = {"bold": True, "foregroundColor": {"red": 1.0, "green": 0.0, "blue": 0.0}}
    FMT_DEFAULT = {}

    sorted_keys = sorted(answers_log.keys())
    text_segments = []  # list of (text_str, format_dict)

    for i, r_idx in enumerate(sorted_keys):
        result = answers_log[r_idx]
        is_correct = result.get('correct')
        answer_text = str(result.get('answer', ''))
        correct_text = "FALSE" if is_correct is False else "TRUE" if is_correct is True else "N/A"

        if i > 0:
            text_segments.append(("\n", FMT_DEFAULT))

        # Question label: bold orange
        q_type_str = result.get('type', '')
        text_segments.append((f"Q{r_idx} ({q_type_str}): ", FMT_ORANGE))

        # Actual answer: bold red if wrong, default if correct
        text_segments.append((answer_text, FMT_RED if is_correct is False else FMT_DEFAULT))

        # " | Correct: " separator: default
        text_segments.append((" | Correct: ", FMT_DEFAULT))

        # Correct value: bold red if FALSE, default if TRUE
        text_segments.append((correct_text, FMT_RED if is_correct is False else FMT_DEFAULT))

    # Build plain string + textFormatRuns
    full_text = ""
    format_runs = []
    pos = 0
    prev_fmt = None

    for text, fmt in text_segments:
        if fmt != prev_fmt:
            format_runs.append({"startIndex": pos, "format": fmt})
            prev_fmt = fmt
        full_text += text
        pos += len(text)

    # 7. Write rich text into the last appended row, column H (0-based index 7)
    last_row = len(worksheet.get_all_values())
    row_idx_0 = last_row - 1

    sh.batch_update({"requests": [{
        "updateCells": {
            "rows": [{
                "values": [{
                    "userEnteredValue": {"stringValue": full_text},
                    "textFormatRuns": format_runs
                }]
            }],
            "fields": "userEnteredValue,textFormatRuns",
            "range": {
                "sheetId": worksheet.id,
                "startRowIndex": row_idx_0,
                "endRowIndex": row_idx_0 + 1,
                "startColumnIndex": 7,  # column H
                "endColumnIndex": 8,
            }
        }
    }]})

#def check_if_taken(email):
#    if not os.path.exists(RESULTS_FILE):
#        return False
#    try:
#        df = pd.read_csv(RESULTS_FILE)
#        if 'Email' in df.columns:
#            # Check if email exists (case insensitive)
#            return email.lower().strip() in df['Email'].str.lower().str.strip().values
#    except:
#        return False
#   return False

# --- APP SETUP ---
st.set_page_config(page_title="Assessment Portal", layout="centered")

# Initialize Session State
if 'page' not in st.session_state:
    st.session_state['page'] = 'login'

# ==================================================
# PAGE 1: CANDIDATE LOGIN
# ==================================================
if st.session_state['page'] == 'login':
    # ADD THIS LINE HERE:
    st.image("sungrow_logo.png", width=200) # Adjust width as neededs
    st.title("🎓 PT1.0 Safety Competency Assessment")
    st.markdown("### Registration")
    
    with st.form("login_form"):
        col1, col2 = st.columns(2)
        
        # Mandatory Fields
        name = col1.text_input("Full Name *")
        email = col2.text_input("Company Email *")
        vendor = col1.text_input("Company Name *")
        instructor = col2.text_input("Instructor Name *")
        
        start = st.form_submit_button("Start Assessment", type="primary")
        
        if start:
            if not (name and email and vendor and instructor):
                st.error("⚠️ All fields are mandatory.")
            #elif check_if_taken(email):
            #    st.error("❌ You have already submitted this assessment.")
            else:
                # Save Candidate Info
                st.session_state['candidate_info'] = {
                    "Name": name, 
                    "Email": email.lower().strip(), 
                    "Company": vendor, 
                    "Instructor": instructor
                }
                
                # Load and Select Questions
                df = load_questions()
                selected_q, points = select_random_questions(df, TARGET_POINTS)
                
                if not selected_q:
                    st.error("Error: No questions loaded. Check your Excel file.")
                else:
                    st.session_state['quiz_data'] = selected_q
                    st.session_state['total_points'] = points
                    st.session_state['page'] = 'quiz'
                    st.rerun()

# ==================================================
# PAGE 2: THE QUIZ
# ==================================================
elif st.session_state['page'] == 'quiz':

    # --- TIMER SETUP ---
    if 'quiz_start_time' not in st.session_state:
        st.session_state['quiz_start_time'] = datetime.now().isoformat()
    start_time = datetime.fromisoformat(st.session_state['quiz_start_time'])
    elapsed    = (datetime.now() - start_time).total_seconds()
    remaining  = max(0, QUIZ_DURATION_SECONDS - elapsed)
    # --- JS countdown timer (real-time display + session keepalive) ---
    components.html(f"""
    <div id='cd' style='font-size:16px;font-weight:bold;color:#1F4E79;font-family:sans-serif;padding:4px 0'></div>
    <script>
    var end = Date.now() + {int(remaining)} * 1000;
    function tick() {{
        var dist = Math.max(0, end - Date.now());
        var m = String(Math.floor(dist/60000)).padStart(2,'0');
        var s = String(Math.floor((dist%60000)/1000)).padStart(2,'0');
        var el = document.getElementById('cd');
        if (dist <= 0) {{
            el.style.color='#C00000';
            el.innerText = '⏰ Time is up! Submitting your answers...';
            try {{ window.parent.window.stAppV2.rerunScript(false); }} catch(e) {{}}
        }} else if (dist < 300000) {{
            el.style.color='#C00000';
            el.innerText = '⏰ Time remaining: ' + m + ':' + s + ' — Please submit now!';
        }} else if (dist < 600000) {{
            el.style.color='#856404';
            el.innerText = '⏳ Time remaining: ' + m + ':' + s;
        }} else {{
            el.style.color='#1F4E79';
            el.innerText = '⏱️ Time remaining: ' + m + ':' + s;
        }}
        if (dist > 0) setTimeout(tick, 1000);
    }}
    tick();
    // Keepalive: trigger Streamlit rerun every 60s
    setInterval(function() {{
        try {{ window.parent.window.stAppV2.rerunScript(false); }} catch(e) {{}}
    }}, 60000);
    </script>
    """, height=40)

    info = st.session_state['candidate_info']
    st.title("📝 Quiz Assessments")
    st.caption(f"Candidate: **{info['Name']}** | Company: **{info['Company']}**")
    
    # Warning if points didn't sum perfectly to 100
    if st.session_state['total_points'] != TARGET_POINTS:
        st.warning(f"Note: Questions total {st.session_state['total_points']} points.")

    with st.form("quiz_form"):
        user_answers = {}
        questions = st.session_state['quiz_data']
        
        for i, q in enumerate(questions):
            st.markdown(f"**{i+1}. {q['Question Text']}** <small>({q['Points']} pts)</small>", unsafe_allow_html=True)
            
            # Identify Type
            q_type = q['Type']
            
            # Get Valid Options (A, B, C, D) - filter out empty ones
            options_map = {}
            for letter in OPTION_COLS:
                if q[letter] != "": # Only add if the cell is not empty
                    options_map[letter] = q[letter]

            # Get just the text values for the buttons
            valid_options_text = list(options_map.values())
            
            # --- RENDER BASED ON TYPE ---
            
            if q_type == 'single': 
                # --- SMART CONTEXT DETECTION ---
                
                # Check: Is "False", "false" present in the options?
                has_boolean_partner = any(str(opt).strip().lower() in ['false', 'f'] for opt in valid_options_text)

                # --- DISPLAY RULES ---
                def format_option(val):
                    s = str(val).strip()
                    s_lower = s.lower()
                    
                    # CASE A: We see "1", but we know it's a Boolean question (because "False" exists)
                    # ACTION: Show "True"
                    if has_boolean_partner and s == '1':
                        return "True"

                    # CASE B: We see "True", but there is NO "False" option.
                    # This implies "True" is an error (it should be the number 1).
                    # ACTION: Show "1"
                    if not has_boolean_partner and s_lower == 'true':
                        return "1"
                        
                    # Default: Show text as-is (e.g., "2", "3", "Blue", "400V")
                    return s

                # 3. Render Radio Button
                user_answers[i] = st.radio(
                    "Select Answer:", 
                    valid_options_text, 
                    key=f"q{i}", 
                    index=None,
                    format_func=format_option 
                )

            elif q_type == 'multi':
                st.write("Select all that apply:")
                user_answers[i] = st.multiselect(
                    "Options:", 
                    valid_options_text, 
                    key=f"q{i}"
                )
                
            elif q_type == 'order':
                st.write("👇 **Select items in the correct order (1st, 2nd, 3rd...):**")
                # Multiselect allows picking order
                user_answers[i] = st.multiselect(
                    "Rank items:", 
                    valid_options_text, 
                    key=f"q{i}"
                )
                
            elif q_type == 'text':
                user_answers[i] = st.text_area("Type your answer:", key=f"q{i}")

        st.markdown("---")
        submitted = st.form_submit_button("Submit Assessment", type="primary")

        if submitted:
            grade_and_submit(questions, user_answers)
            st.session_state['page'] = 'success'
            st.rerun() # Instantly reloads the app

            # ==================================================
            # PAGE 3: SUBMISSION SUCCESS
            # ==================================================
elif st.session_state['page'] == 'success':
    st.title("🎉 Assessment Complete!")
    st.success("Your answers have been successfully recorded. Thank you!")
    st.balloons()
    
    st.markdown("---")
    st.info("You may now close this tab/window.")