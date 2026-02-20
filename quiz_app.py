import streamlit as st
import pandas as pd
import os
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials

# --- CONFIGURATION ---
QUESTIONS_FILE = "question_pool.xlsx"
RESULTS_FILE = "quiz_results.csv"
OPTION_COLS = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']
TARGET_POINTS = 100

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
            df[col] = df[col].astype(str).replace('nan', '').str.strip()
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
        worksheet = sh.worksheet("ST556kWh_CSP_Safety") # consistently gets the corresponding tab
    except Exception as e:
        st.error(f"Google Sheets Connection Error: {e}")
        st.stop()
    
    # 5. Prepare and Append Row
    row = [
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        candidate_info['Name'],
        candidate_info['Email'],
        candidate_info['Vendor'],
        candidate_info['Instructor'],
        score,
        max_score,
        str(answers_log)
    ]
    
    # 5. Append to Sheet
    worksheet.append_row(row)

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
#    return False

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
    st.title("🎓 ST556kWh-250UD Safety Competency Assessment")
    st.markdown("### Registration")
    
    with st.form("login_form"):
        col1, col2 = st.columns(2)
        
        # Mandatory Fields
        name = col1.text_input("Full Name *")
        email = col2.text_input("Company Email *")
        vendor = col1.text_input("Vendor Name *")
        instructor = col2.text_input("Instructor Name *")
        
        start = st.form_submit_button("Start Assessment", type="primary")
        
        if start:
            if not (name and email and vendor and instructor):
                st.error("⚠️ All fields are mandatory.")
            else:
                # Save Candidate Info
                st.session_state['candidate_info'] = {
                    "Name": name, 
                    "Email": email.lower().strip(), 
                    "Vendor": vendor, 
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
    info = st.session_state['candidate_info']
    st.title("📝 Quiz Assessments")
    st.caption(f"Candidate: **{info['Name']}** | Vendor: **{info['Vendor']}**")
    
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

            # Create list of texts for display
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
            score = 0
            details_log = {}
            
            for i, q in enumerate(questions):
                u_ans = user_answers.get(i) # User's answer (Text or List of Texts)
                q_type = q['Type']
                points = q['Points']
                r_idx = q['row_index'] # The 1-based Excel ID

                # Setup Option Map
                options_map = {letter: q.get(letter, "") for letter in OPTION_COLS if str(q.get(letter, "")).strip() != ""}

                # Parse Correct Answer Key (e.g., "A, C" or "B")
                c_key_str = str(q['Correct Answer']).upper()
                c_keys = [x.strip() for x in c_key_str.split(',')]
                
                # Retrieve the ACTUAL TEXT of the correct options from the Excel row
                # Example: if Correct Answer is 'A', we need the text in column 'A'
                correct_texts = []
                for k in c_keys:
                    if k in options_map:
                        correct_texts.append(options_map[k])
                
                # --- GRADING LOGIC ---
                
                is_correct = False
                
                if q_type == 'single':
                    # User sends single string. Check if it matches correct text.
                    if str(u_ans) == str(correct_texts[0]) if correct_texts else False:
                        is_correct = True
                        
                elif q_type == 'multi':
                    # User sends list. Order DOES NOT matter. Use sorted()
                    if sorted(u_ans) == sorted(correct_texts):
                        is_correct = True
                        
                elif q_type == 'order':
                    # User sends list. Order DOES matter. Compare directly.
                    if u_ans == correct_texts:
                        is_correct = True
                        
                elif q_type == 'text':
                    is_correct = None
                    # No auto-grading. Just Log.
                    # details_log[f"Q{i+1}"] = f"[ANSWER]: {u_ans}"
                    # continue # Skip the score addition part
                
                # Apply Score
                if is_correct:
                    score += points
                    
                details_log[r_idx] = {
                    "answer": u_ans,
                    "correct": is_correct,
                    "type": q_type.capitalize()
                }

            # SAVE
            save_submission(
                st.session_state['candidate_info'], 
                score, 
                st.session_state['total_points'], 
                details_log
            )
            
             # --- ROUTE TO FINISH PAGE ---
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