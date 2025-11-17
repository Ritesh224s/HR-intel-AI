import streamlit as st
import google.generativeai as genai
from pymongo import MongoClient
from datetime import datetime
import json
import re 
from typing import Optional, Dict, Any

# ==============================================================================
# 1. CONFIGURATION
# ==============================================================================

# --- API Keys and URIs ---
GEMINI_API_KEY = "AIzaSyBI_xPmr-kMb-70pTgmivWw47Kr7xdzxgY"
# 🚨 CRITICAL: REPLACE '1234' with your correct MongoDB Atlas password
MONGO_URI = "mongodb+srv://riteshdeshmukh224s:1234@clustertest.na8lqvg.mongodb.net/" 
CHAT_DB_NAME = "hr_chat_db"
CHAT_COLLECTION_NAME = "chats"
DATA_DB_NAME = "employees_info"
DATA_COLLECTION_NAME = "employes"
# -----------------------------

# Configure Gemini
try:
    genai.configure(api_key=GEMINI_API_KEY)
except Exception as e:
    # This won't run in the canvas environment but is good practice
    pass

# --- MongoDB Connection ---
@st.cache_resource
def init_connection():
    """Initializes MongoDB connection and returns collections."""
    try:
        client = MongoClient(MONGO_URI)
        chat_db = client[CHAT_DB_NAME]
        chats_collection = chat_db[CHAT_COLLECTION_NAME]
        data_db = client[DATA_DB_NAME] 
        employee_collection = data_db[DATA_COLLECTION_NAME] 
        return True, chats_collection, employee_collection, None
    except Exception as e:
        return False, None, None, str(e)

mongo_status, chats_collection, employee_collection, mongo_error_msg = init_connection()

# Initialize session state for login
if 'logged_in' not in st.session_state:
    st.session_state['logged_in'] = False
if 'username' not in st.session_state:
    st.session_state['username'] = None
if 'role' not in st.session_state:
    st.session_state['role'] = None
if 'employee_id' not in st.session_state:
    st.session_state['employee_id'] = None
if 'employee_data' not in st.session_state:
    st.session_state['employee_data'] = None


# ==============================================================================
# 2. AUTHENTICATION LOGIC & DATA MAPPING (Updated for specific schema keys)
# ==============================================================================

# Static users for Admin/HR Manager roles (not in the main employee table)
STATIC_USERS = {
    "admin123": {"password": "adminpassword", "username": "Super Admin", "role": "Admin", "employee_id": "ADM-001"},
    "manager456": {"password": "hrpassword", "username": "HR Manager Team", "role": "HR Manager", "employee_id": "HRM-001"},
}

def authenticate_user(login_id, password, employee_collection):
    """
    Authenticates user against static list or MongoDB employee records.
    Returns (True, user_details) or (False, None).
    """
    # 1. Check Static Users (Admin/HR Manager)
    if login_id in STATIC_USERS and STATIC_USERS[login_id]["password"] == password:
        return True, STATIC_USERS[login_id]

    # 2. Check MongoDB Employees (Role: Employee)
    if mongo_status:
        try:
            # Search using the key "login id" (note the space, as per your schema)
            employee_doc = employee_collection.find_one({
                "login id": login_id, 
            })
            
            if employee_doc:
                # Check both password and passwords fields for compatibility
                db_password = employee_doc.get("password") or employee_doc.get("passwords")
                
                if db_password == password:
                    employee_doc.pop('_id', None) # Remove MongoDB _id for cleaner JSON/data handling
                    
                    # --- Mapping to Session State based on your schema ---
                    # employee_id might be missing for some (like Ritesh), so we default to N/A
                    employee_id = employee_doc.get("employee_id", "N/A") 
                    employee_name = employee_doc.get("employee_name")
                    
                    return True, {
                        "username": employee_name,
                        "role": "Employee",
                        "employee_id": employee_id,
                        "login_id": login_id,
                        # Store the full document with correct schema keys
                        "employee_data": employee_doc 
                    }
        except Exception:
            # Error during DB access
            return False, None

    return False, None


def handle_login():
    """Handles the Streamlit login form submission."""
    login_id = st.session_state.login_id_input
    password = st.session_state.password_input
    
    # Reset state
    st.session_state['logged_in'] = False
    st.session_state['username'] = None
    st.session_state['role'] = None
    st.session_state['employee_id'] = None
    st.session_state['employee_data'] = None
    
    if not login_id or not password:
        st.error("Please enter both Login ID and Password.")
        return

    success, user_details = authenticate_user(login_id, password, employee_collection)

    if success:
        st.session_state['logged_in'] = True
        st.session_state['username'] = user_details['username']
        st.session_state['role'] = user_details['role']
        st.session_state['employee_id'] = user_details.get('employee_id')
        st.session_state['employee_data'] = user_details.get('employee_data') 
        
        st.success(f"Login successful! Welcome, {user_details['username']} ({user_details['role']}).")
        st.session_state.login_id_input = ""
        st.session_state.password_input = ""
        st.rerun()
    else:
        st.error("Invalid Login ID or Password.")

def handle_logout():
    """Handles user logout."""
    st.session_state['logged_in'] = False
    st.session_state['username'] = None
    st.session_state['role'] = None
    st.session_state['employee_id'] = None
    st.session_state['employee_data'] = None
    st.rerun()

# ==============================================================================
# 3. CORE CHAT FUNCTIONS (RAG & System Instruction)
# ==============================================================================

def get_mongo_context(user_query, employee_collection):
    """
    Searches the employees collection for relevant data based on the user's query (RAG).
    """
    
    query_lower = user_query.lower()
    search_value: Optional[str] = None
    search_key: Optional[str] = None
    
    # 1. Search by Employee ID (e.g., E-101)
    id_match = re.search(r'(e-\d{3})', query_lower) or re.search(r'(e\d{3})', query_lower)
    
    if id_match:
        search_value = id_match.group(0).upper().replace('-', '') # E101 or E-101
        search_key = "employee_id"
        
    else:
        # 2. Search by Employee Name (based on names provided in schema)
        name_search_map: Dict[str, str] = {
            "ritesh": "Ritesh Deshmukh", "vedant": "Vedant Bonde", 
            "rihan": "Rihan Khan", "siddhant": "Siddhant Sharma",
            "vaibhav": "Vaibhav Verma", "deshmukh": "Ritesh Deshmukh",
            "bonde": "Vedant Bonde", "khan": "Rihan Khan", 
            "sharma": "Siddhant Sharma", "verma": "Vaibhav Verma", 
        }
        
        # Use the exact DB key "employee_name" for searching
        search_key = "employee_name" 
        for key, value in name_search_map.items():
            if key in query_lower:
                search_value = value
                break
            
    
    if search_value and search_key:
        try:
            employee_doc: Optional[Dict[str, Any]] = employee_collection.find_one({search_key: search_value})
            
            if employee_doc:
                employee_doc.pop('_id', None) 
                # Format the data into a JSON string for Gemini
                context_string = f"EMPLOYEE HR RECORD (JSON): {json.dumps(employee_doc, indent=2)}"
                return context_string
        except Exception:
            return None
            
    return None 

def generate_system_instruction(role: str, rag_used: bool) -> str:
    """Generates a tailored system instruction based on the user's role and RAG usage."""
    base_instruction = f"You are a highly professional and confidential HR Assistant for a large corporation. Your current user is logged in as a **{role}**."
    
    if rag_used:
        # Instruction for when internal data is provided
        return (
            f"{base_instruction} "
            "Answer the user's question STRICTLY and ONLY based on the CONTEXT (EMPLOYEE HR RECORD) provided below. "
            "Provide clear, factual information about the employee's details (e.g., title, department, salary). "
            "If the specific answer is not in the context, state 'I cannot find that specific employee data in the HR records.'."
            "Maintain strict confidentiality."
        )
    else:
        # General knowledge instruction
        if role == "Employee":
            return f"{base_instruction} Your answers should be friendly, clear, and focused on general policy, benefits, or common HR FAQs."
        elif role == "HR Manager":
            return f"{base_instruction} Your answers should be concise, professional, and focus on policy interpretation, reporting, or organizational guidelines."
        elif role == "Admin":
            return f"{base_instruction} Your answers should be high-level, strategic, and focus on system capabilities or high-level policy guidance."
        else:
            return base_instruction

def clear_chat_history(collection):
    """Clears all documents from the chats collection and refreshes the app."""
    try:
        # Clear all chats for Manager/Admin tools
        collection.delete_many({}) 
        st.session_state.user_query = "" # Clear pending query
        st.session_state.run_consult = False # Clear pending run
        st.rerun() 
    except Exception as e:
        st.error(f"Error clearing history: {e}")

# ==============================================================================
# 4. STREAMLIT UI LAYOUT & DISPLAY LOGIC 
# ==============================================================================

st.set_page_config(page_title="Secured Gemini + MongoDB HR Assistant", layout="wide")


# --- Custom CSS for Styling ---
st.markdown("""
<style>
.block-container { padding-top: 2rem; padding-bottom: 2rem; }
[data-testid="stVerticalBlock"] { gap: 0.5rem; }
h1 { color: #4CAF50; text-align: center; }
.stTextInput > div > div > input {
    border-radius: 15px; 
    padding: 10px;
    height: 50px; 
    border: 2px solid #4CAF50;
    font-size: 1.1em;
}

/* User Info Card (The Summary Card) */
.info-card {
    background-color: #E8F5E9; /* Very light green/mint */
    padding: 20px;
    border-radius: 12px;
    margin-bottom: 20px;
    border-left: 5px solid #4CAF50;
    box-shadow: 0 4px 8px rgba(0,0,0,0.1);
}
.info-card h3 {
    color: #388E3C; 
    margin-top: 0;
}
/* Key-Value Pair Styling (Vertical layout fix) */
.info-card p {
    margin: 5px 0;
    font-size: 1.05em;
    color: #333;
    /* Ensure each detail is on its own line */
    display: block; 
}
.info-card span {
    font-weight: bold;
    color: #000;
    /* Separate key and value */
    margin-left: 5px; 
}


/* Chat Message Bubbles - Improved Readability for Dark Theme */

/* User Message Bubble (Used a brighter, highly contrasting background) */
.user-bubble {
    background-color: #A5D6A7; /* Medium light green for better visibility */
    color: #1B5E20; /* Very dark green text for maximum contrast */
    border-radius: 15px 15px 5px 15px; 
    padding: 10px 15px;
    margin: 10px 0;
    margin-left: 20%; 
    width: fit-content;
    max-width: 80%;
    float: right;
    clear: both;
    box-shadow: 0 2px 4px rgba(0,0,0,0.2);
}
.user-bubble h5, .user-bubble p, .user-bubble small {
    color: #1B5E20 !important; /* Ensure all text within is dark */
}

/* Assistant Message Bubble (Used white background with dark text) */
.assistant-bubble {
    background-color: #FFFFFF; /* White background for maximum contrast */
    color: #000000; /* Black text */
    border-radius: 15px 15px 15px 5px; 
    padding: 10px 15px;
    margin: 10px 0;
    margin-right: 20%; 
    width: fit-content;
    max-width: 80%;
    float: left;
    clear: both;
    box-shadow: 0 2px 4px rgba(0,0,0,0.2);
    border: 1px solid #4CAF50; /* Clear green border */
}
.assistant-bubble h5, .assistant-bubble p, .assistant-bubble small {
    color: #000000 !important; /* Ensure all text within is black */
}

.history-container {
    padding: 10px; 
    max-height: 500px;
    overflow-y: auto;
}
</style>
""", unsafe_allow_html=True)


st.title("🔒 Secured AI-Powered HR Data Portal")

# --- Function to display user's current info card (Vertical Layout) ---
def display_user_summary_card(user_data):
    """
    Displays the currently logged-in user's relevant HR information in a vertical list.
    """
    
    username = user_data.get('username', 'N/A')
    role = user_data.get('role', 'N/A')
    
    st.subheader("Your Personal HR Summary")
    
    st.markdown(f'<div class="info-card">', unsafe_allow_html=True)
    
    if role == "Employee":
        employee_id = user_data.get('employee_id', 'N/A')
        employee_record = user_data.get('employee_data', {})
        
        # Data structure for vertical display
        details = [
            ("Employee Name", username),
            ("Role", role),
            ("Job Title", employee_record.get('title', 'N/A')),
            ("Department", employee_record.get('department', 'N/A')), 
            ("Salary", f"₹{employee_record.get('salary', 'N/A'):,}" if isinstance(employee_record.get('salary'), (int, float)) else employee_record.get('salary', 'N/A')),
            ("Hire Date", employee_record.get('hire_date', 'N/A')),
            ("Status", employee_record.get('status', 'N/A')),
            ("Employee ID", employee_id),
        ]
        
        st.markdown(f"<h3>{username}'s Details</h3>", unsafe_allow_html=True)
        
        for label, value in details:
            if value is not None and value != 'N/A':
                # Using markdown for HTML structure to enforce vertical display
                st.markdown(f"<p><b>{label}:</b><span>{value}</span></p>", unsafe_allow_html=True)
    
    elif role in ["Admin", "HR Manager"]:
        st.markdown(f"<h3>Welcome, {username}</h3>", unsafe_allow_html=True)
        st.markdown(f"<p><b>Access Level:</b><span>{role}</span></p>", unsafe_allow_html=True)
        st.markdown(f"<p>Note: You have access to all employee records via the chat assistant.</p>", unsafe_allow_html=True)

    st.markdown(f'</div>', unsafe_allow_html=True)

# --- Sidebar: Login/Logout & Clear Chat ---
st.sidebar.header("🔑 Access Panel")

if st.session_state['logged_in']:
    # Logged In View
    st.sidebar.success(f"Welcome, {st.session_state['username']}!")
    st.sidebar.info(f"**Role:** {st.session_state['role']}")
    st.sidebar.button("🔓 Logout", on_click=handle_logout, use_container_width=True)
    
    st.sidebar.markdown("---")
    
    # --- Clear Chat Button (In the sidebar) ---
    role = st.session_state['role']
    if role in ["HR Manager", "Admin"] and mongo_status:
        if st.sidebar.button("🗑️ Clear Chat History", type="secondary", use_container_width=True):
            clear_chat_history(chats_collection)
        st.sidebar.markdown("---")

    st.sidebar.markdown("### Database Status")
    if mongo_status:
        st.sidebar.success("MongoDB: ONLINE ✅")
    else:
        st.sidebar.error(f"MongoDB: OFFLINE ❌\n\nError: {mongo_error_msg}")

else:
    # Login Form View
    st.sidebar.markdown("Please log in to access the HR data and consultation tool.")
    
    with st.sidebar.form("login_form"):
        st.text_input("Login ID", key="login_id_input") 
        st.text_input("Password", type="password", key="password_input")
        st.form_submit_button("Login", on_click=handle_login)
    
    # st.sidebar.markdown("---")
    # st.sidebar.caption("Employee Logins (from DB):")
    # st.sidebar.caption("- Ritesh: `Ritesh_deshmukh8459` / `user_name@2323`")
    # st.sidebar.caption("- Vedant: `Vedant_Bonde675` / `vedant_pass123`")
    # st.sidebar.caption("Admin Logins (Static):")
    # st.sidebar.caption("- Admin: `admin123` / `adminpassword`")
    
# ==============================================================================
# 5. PROTECTED MAIN CONTENT
# ==============================================================================

if st.session_state['logged_in']:
    username = st.session_state['username']
    role = st.session_state['role']
    model_name = "gemini-2.5-flash"

    # --- Display User's Immediate Info Card ---
    user_info = {
        'username': st.session_state['username'],
        'role': st.session_state['role'],
        'employee_id': st.session_state['employee_id'],
        'employee_data': st.session_state['employee_data']
    }
    display_user_summary_card(user_info)

    # --- Chatbot Section ---
    
    if 'run_consult' not in st.session_state:
        st.session_state['run_consult'] = False
    if 'user_query' not in st.session_state:
        st.session_state['user_query'] = ""

    def set_run_consult(query):
        st.session_state['run_consult'] = True
        st.session_state['user_query'] = query
        st.session_state.user_question_input = "" 
    
    st.subheader(f"Ask about Employees or HR Policy 📋 (Role: {role})")
    
    # --- Chat Input ---
    with st.container():
        col1, col2 = st.columns([4, 1]) 
        
        with col1:
            user_input = st.text_input("Your question:", label_visibility="collapsed", key="user_question_input", 
                                       placeholder="e.g., 'What is Ritesh Deshmukh's salary?'")
            
        with col2:
            st.button("Consult 🚀", use_container_width=True, type="primary", on_click=set_run_consult, args=(user_input,))

    # --- Logic to generate and display the response ---
    if st.session_state.get('run_consult') and st.session_state.get('user_query'):
        user_query = st.session_state['user_query']
        
        if user_query:
            try:
                # 1. RAG Retrieval
                mongo_context = get_mongo_context(user_query, employee_collection)
                
                # 2. Set Instructions based on RAG
                rag_used = mongo_context is not None
                system_instruction = generate_system_instruction(role, rag_used)
                
                # Prepare Full Prompt
                full_prompt = f"QUESTION: {user_query}"
                if rag_used:
                    st.info("🔍 Employee data found! Using **RAG** for specific HR information.")
                    full_prompt = f"CONTEXT: {mongo_context}\n\n{full_prompt}"
                else:
                    st.info("🌍 No specific employee data found. Answering with general HR or company policy knowledge.")

                # 3. Gemini Generation
                with st.spinner("Consulting HR Assistant..."):
                    model = genai.GenerativeModel(model_name, system_instruction=system_instruction)
                    response = model.generate_content(full_prompt)

                # 4. Display Response and Log Chat
                if response and response.text:
                    answer = response.text
                    
                    # Display the response in a clearly styled box for better contrast
                    st.markdown(f'<div class="chat-container">🤖 **HR Assistant:** {answer}</div>', unsafe_allow_html=True)

                    if mongo_status:
                        chat_data = {
                            "username": username, "role": role, 
                            "question": user_query, "answer": answer, 
                            "timestamp": datetime.now(), "rag_used": rag_used
                        }
                        chats_collection.insert_one(chat_data)
                else:
                    st.warning("⚠️ Gemini did not return any answer.")
            except Exception as e:
                st.error(f"Gemini API Error: {e}")
                
        # Reset flag after processing to avoid re-running immediately
        st.session_state['run_consult'] = False
        st.rerun()


    # -------------------- 6. CHAT HISTORY LOG --------------------

    st.markdown("---")
    st.subheader("🕓 Chat History Log")
        
    if mongo_status:
        # Define the query based on the user's role
        if role in ["HR Manager", "Admin"]:
            st.markdown("Viewing all employee/HR consultations (Manager/Admin access)")
            query = {}
        else:
            st.markdown(f"Viewing only your consultation history (Logged in as: **{username}**)")
            query = {"username": username}

        # --- Display History (Using Chat Bubbles) ---
        with st.container():
            st.markdown('<div class="history-container">', unsafe_allow_html=True)
            
            try:
                # Fetch chats based on the defined query
                chats_list = list(chats_collection.find(query).sort("timestamp", -1).limit(50))
            except Exception as e:
                st.error(f"Error fetching history: {e}")
                chats_list = [] 
            
            if not chats_list: 
                st.markdown("*No chat history yet.*")
            else:
                # Reverse the list so the oldest messages are at the top, like a normal chat
                for chat in reversed(chats_list): 
                    timestamp_str = chat['timestamp'].strftime('%Y-%m-%d %H:%M')
                    
                    # USER QUESTION BUBBLE (Light green bubble, dark text)
                    st.markdown(f"""
                    <div class="user-bubble">
                        <h5 style='margin: 0; padding: 0;'>👤 **{chat['username']}**</h5>
                        <p style='margin: 5px 0 0 0; padding: 0;'>{chat['question']}</p>
                        <small style='float: right; opacity: 0.8; font-weight: bold;'>{timestamp_str}</small>
                        <div style='clear: both;'></div>
                    </div>
                    """, unsafe_allow_html=True)

                    # ASSISTANT ANSWER BUBBLE (White bubble, black text)
                    st.markdown(f"""
                    <div class="assistant-bubble">
                        <h5 style='margin: 0; padding: 0;'>🤖 **HR Assistant**</h5>
                        <p style='margin: 5px 0 0 0; padding: 0;'>{chat['answer']}</p>
                        <small style='float: right; opacity: 0.8; font-weight: bold;'>{timestamp_str}</small>
                        <div style='clear: both;'></div>
                    </div>
                    <div style='clear: both;'></div>
                    """, unsafe_allow_html=True)
                    
            st.markdown('</div>', unsafe_allow_html=True)
    else:
        st.warning("Cannot display history: MongoDB is disconnected.")
        
# --- Welcome/Non-Logged-In Screen ---
else:
    st.markdown(
        """
        <div style="
            padding: 40px;
            border-radius: 12px;
            background-color: #e8f5e9; 
            border: 2px solid #4CAF50; 
            box-shadow: 0 4px 12px rgba(0, 70, 0, 0.2);
            text-align: center;
            margin-top: 80px;
        ">
            <h2 style="color: #388E3C; margin-bottom: 20px;">Secure Access Required</h2>
            <p style="font-size: 1.1em; color: #555;">
                This portal contains confidential Human Resources data.
            </p>
            <p style="font-size: 1.2em; color: #333; font-weight: bold;">
                Please use the **Access Panel** on the left to log in.
            </p>
            <p style="font-size: 0.9em; color: #777; margin-top: 30px;">
                Only authenticated users can consult the HR AI Assistant.
            </p>
        </div>
        """, 
        unsafe_allow_html=True

    )
