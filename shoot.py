import streamlit as st
import requests
from bs4 import BeautifulSoup
import pandas as pd
import re
import io
import math

st.set_page_config(page_title="TECMIS Attendance Tracker", layout="wide")
st.title(" TECMIS Attendance Tracker")
st.info("💡 **Note:** Student usernames require the **'sc'** prefix (e.g., `sc5291`).")

# --- CONFIGURATION ---
BASE_URL = "https://paravi.ruh.ac.lk/tecmis/"
LOGIN_URL = "https://paravi.ruh.ac.lk/tecmis/login.php"
MENU_URL = "https://paravi.ruh.ac.lk/tecmis/index.php?view=admin&admin=22"
ATT_URL = "https://paravi.ruh.ac.lk/tecmis/index.php?view=admin&admin=22&task=viewAtt"

FIELD_USERNAME = "uname"
FIELD_PASSWORD = "upwd"

# --- UI INPUTS ---
col1, col2 = st.columns(2)
with col1:
    user = st.text_input("Username (e.g., sc5291)")
with col2:
    pwd = st.text_input("Password", type="password")

if st.button(" Login & Fetch All Attendance", type="primary"):
    if not user or not pwd:
        st.error("Please enter both username and password.")
    else:
        with st.spinner("Authenticating..."):
            try:
                session = requests.Session()
                session.headers.update({
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                })

                session.get(BASE_URL, verify=True, timeout=10)
                payload = {FIELD_USERNAME: user, FIELD_PASSWORD: pwd}
                post_response = session.post(LOGIN_URL, data=payload, verify=True, timeout=10)

                if "Sign In" in post_response.text and 'name="uname"' in post_response.text:
                    st.error("❌ Login failed. Check your username (remember the 'sc' prefix) and password.")
                else:
                    st.success("✅ Login successful! Fetching subject list...")

                    menu_response = session.get(MENU_URL, verify=True, timeout=10)
                    soup_menu = BeautifulSoup(menu_response.text, 'html.parser')
                    forms = soup_menu.find_all('form', id=re.compile(r'^attform-std'))

                    if not forms:
                        st.warning("⚠️ Could not find any subjects.")
                    else:
                        subjects_data = []
                        for form in forms:
                            course_id_input = form.find('input', {'name': 'course_id'})
                            course_input = form.find('input', {'name': 'course'})

                            if course_id_input and course_input:
                                tr = form.find_parent('tr')
                                row_text = tr.get_text(separator='|', strip=True)
                                parts = [p.strip() for p in row_text.split('|') if p.strip()]

                                subject_code = parts[0] if len(parts) > 0 else course_input.get('value')
                                subject_name = parts[1] if len(parts) > 1 else course_input.get('value')

                                subjects_data.append({
                                    'code': subject_code,
                                    'name': subject_name,
                                    'course_id': course_id_input.get('value'),
                                    'course_param': course_input.get('value')
                                })

                        st.info(f"Found {len(subjects_data)} subjects. Fetching attendance for each...")

                        results = []
                        debug_html = ""
                        progress_bar = st.progress(0)

                        for i, subj in enumerate(subjects_data):
                            progress_bar.progress((i + 1) / len(subjects_data))

                            att_payload = {
                                'course_id': subj['course_id'],
                                'course': subj['course_param']
                            }
                            att_response = session.post(ATT_URL, data=att_payload, verify=True, timeout=10)
                            att_soup = BeautifulSoup(att_response.text, 'html.parser')

                            best_percent = "N/A"
                            total_hours = 0
                            present_hours = 0

                            # Find the attendance table
                            all_tables = att_soup.find_all('table')
                            attendance_table = None

                            for table in all_tables:
                                table_text = table.get_text(' ', strip=True).lower()
                                if 'date' in table_text and 'status' in table_text:
                                    attendance_table = table
                                    break

                            if attendance_table:
                                if i == 0:
                                    debug_html = str(attendance_table)

                                try:
                                    # Parse table using Pandas
                                    df_att = pd.read_html(io.StringIO(str(attendance_table)))[0]
                                    df_att = df_att.dropna(how='all')

                                    # Remove footer if it exists
                                    if len(df_att) > 0 and 'total' in str(df_att.iloc[-1, 0]).lower():
                                        df_att = df_att.iloc[:-1]

                                    # Identify columns: Hours is index 2, Status is the last column
                                    hours_col = df_att.columns[2]
                                    status_col = df_att.columns[-1]

                                    # Convert Hours to numeric (handles strings safely)
                                    df_att[hours_col] = pd.to_numeric(df_att[hours_col], errors='coerce').fillna(0)

                                    # Remove header row if Pandas accidentally included it as data
                                    if df_att.iloc[0][hours_col] == 0 and 'hours' in str(
                                            df_att.iloc[0][status_col]).lower():
                                        df_att = df_att.iloc[1:]

                                    # Calculate Hours
                                    total_hours = df_att[hours_col].sum()
                                    present_hours = \
                                    df_att[df_att[status_col].astype(str).str.lower().str.contains('present')][
                                        hours_col].sum()

                                    if total_hours > 0:
                                        best_percent = f"{(present_hours / total_hours) * 100:.1f}%"

                                except Exception:
                                    # Fallback: Manual parsing if Pandas fails
                                    rows = attendance_table.find_all('tr')
                                    for row in rows:
                                        cells = row.find_all('td')
                                        if len(cells) == 7:
                                            try:
                                                hours = int(cells[2].get_text(strip=True))
                                            except ValueError:
                                                hours = 0
                                            status = cells[6].get_text(strip=True).lower()

                                            total_hours += hours
                                            if 'present' in status:
                                                present_hours += hours

                                    if total_hours > 0:
                                        best_percent = f"{(present_hours / total_hours) * 100:.1f}%"

                            # --- Calculate Hours you can cut ---
                            if total_hours > 0:
                                # Formula: x = floor( (Present Hours / 0.80) - Total Hours )
                                max_cut_hours = (present_hours / 0.80) - total_hours
                                hours_to_cut = int(math.floor(max_cut_hours))

                                if hours_to_cut < 0:
                                    cut_text = "0 (Below 80%)"
                                else:
                                    cut_text = f"{hours_to_cut} hours"
                            else:
                                hours_to_cut = 0
                                cut_text = "N/A"
                            # ----------------------------------

                            results.append({
                                'Subject Code': subj['code'],
                                'Subject Name': subj['name'],
                                'Attendance': best_percent,
                                'Hours Attended': f"{present_hours}/{total_hours}",
                                'Hours you can cut': cut_text
                            })

                        progress_bar.empty()

                        if results:
                            df = pd.DataFrame(results)

                            # Clean the Attendance column for charting
                            df['Percentage_Value'] = df['Attendance'].astype(str).str.replace('%', '').str.strip()
                            df['Percentage_Value'] = pd.to_numeric(df['Percentage_Value'], errors='coerce')

                            st.subheader("📊 Your Attendance Summary (Calculated by Hours)")
                            st.dataframe(df[['Subject Code', 'Subject Name', 'Attendance', 'Hours Attended',
                                             'Hours you can cut']], use_container_width=True, height=400)

                            with st.expander(" DEBUG: Click here if calculations are wrong"):
                                st.write("Here is the exact HTML table the code found for your **first subject**:")
                                st.code(debug_html[:1500] + "...", language="html")

                            # Visualization
                            st.subheader("📈 Attendance Visualization")
                            chart_df = df.dropna(subset=['Percentage_Value']).copy()
                            chart_df = chart_df.set_index('Subject Code')

                            if not chart_df.empty:
                                st.bar_chart(chart_df[['Percentage_Value']])

            except requests.exceptions.RequestException as e:
                st.error(f"🌐 Network error: {str(e)}")
            except Exception as e:
                st.error(f"⚠️ An unexpected error occurred: {str(e)}")