import streamlit as st
import pandas as pd
import numpy as np
import json
import io

# --- CONFIGURATION & SETUP ---
st.set_page_config(page_title="Chimney Shell Design", layout="wide")

def init_session_state():
    if 'project_data' not in st.session_state:
        st.session_state.project_data = None
    if 'generated' not in st.session_state:
        st.session_state.generated = False

init_session_state()

# --- HELPER FUNCTIONS ---

def get_sigma_cbc(grade_str):
    # Approximation of IS:456 Permissible Compressive Stress
    # User can override in advanced settings if needed
    mapping = {'M20': 7, 'M25': 8.5, 'M30': 10, 'M35': 11.5, 'M40': 13}
    return mapping.get(grade_str, 10)

def calculate_frustum_volume(h, r1_out, r1_in, r2_out, r2_in):
    """
    Calculates volume of a hollow frustum (tapered cylinder).
    Level 1 (Top), Level 2 (Bottom)
    """
    # Volume of Outer Cone Frustum
    vol_out = (np.pi * h / 3) * (r1_out**2 + r1_out * r2_out + r2_out**2)
    # Volume of Inner Cone Frustum
    vol_in = (np.pi * h / 3) * (r1_in**2 + r1_in * r2_in + r2_in**2)
    return vol_out - vol_in

# --- MAIN APP LOGIC ---

st.title("🏭 RC Chimney Shell Load Analysis")
st.markdown("---")

# ==========================================
# 1. SIDEBAR - INPUTS
# ==========================================
with st.sidebar:
    st.header("1. Global Geometry")
    
    # File Operations
    st.subheader("File Operations")
    uploaded_file = st.file_uploader("Open Project (JSON)", type=['json'])
    
    st.divider()
    
    total_height = st.number_input("Total Height (m)", value=30.0, step=1.0)
    segment_height = st.number_input("Segment Height (m)", value=2.5, step=0.5)
    top_inner_dia = st.number_input("Top Inner Dia (m)", value=1.35)
    
    # Slope handling (1 in X)
    slope_type = st.radio("Slope Type", ["Vertical (Cylindrical)", "Tapered"])
    slope_val = 0.0
    if slope_type == "Tapered":
        slope_ratio = st.number_input("Slope (1 in X)", value=50.0)
        if slope_ratio > 0:
            slope_val = 1 / slope_ratio
            
    default_thickness = st.number_input("Default Shell Thickness (m)", value=0.20)
    
    st.header("2. Materials")
    conc_grade = st.selectbox("Concrete Grade", ["M20", "M25", "M30", "M35", "M40"], index=2)
    conc_density = st.number_input("Conc. Density (t/m3)", value=2.5)
    
    st.header("3. Actions")
    if st.button("Generate/Reset Grid", type="primary"):
        # Generate geometric levels
        levels = []
        current_h = total_height
        
        while current_h >= -0.1: # Go slightly below 0 to catch the raft level
            # Calculate ID at this height
            # Depth from top
            depth = total_height - current_h
            # Increase in radius = depth * slope
            radius_increase = depth * slope_val
            
            curr_id = top_inner_dia + (2 * radius_increase)
            curr_od = curr_id + (2 * default_thickness)
            
            levels.append({
                "Level (m)": round(current_h, 3),
                "Outer Dia (m)": round(curr_od, 4),
                "Inner Dia (m)": round(curr_id, 4),
                "Thickness (m)": default_thickness,
                "Density (t/m3)": conc_density,
                "Platform Load (t)": 0.0,
                "Liner Load (t)": 0.0,
                "Corbel Load (t)": 0.0
            })
            
            # Logic to handle the last segment (raft)
            if current_h == 0:
                break
            next_h = current_h - segment_height
            if next_h < 0:
                next_h = 0
            current_h = next_h
            
        st.session_state.project_data = pd.DataFrame(levels)
        st.session_state.generated = True

# ==========================================
# LOAD LOGIC (File Open)
# ==========================================
if uploaded_file is not None and st.session_state.project_data is None:
    try:
        data = json.load(uploaded_file)
        st.session_state.project_data = pd.DataFrame(data['grid'])
        st.session_state.generated = True
        st.success("Project Loaded Successfully!")
    except Exception as e:
        st.error(f"Error loading file: {e}")

# ==========================================
# 2. MAIN INTERFACE - TABS
# ==========================================

if st.session_state.generated:
    
    df = st.session_state.project_data
    
    tab1, tab2, tab3 = st.tabs(["📝 Edit Grid & Loads", "🧮 Calculation Results", "💾 Save / Export"])
    
    # --- TAB 1: EDIT GRID ---
    with tab1:
        st.info("Instructions: Edit the grid below for specific segment overrides (e.g., Stainless Steel top section density, or specific Platform Loads). Calculations update automatically in the next tab.")
        
        # Using Data Editor to allow user to tweak the generated geometry
        edited_df = st.data_editor(
            df,
            num_rows="dynamic",
            height=600,
            use_container_width=True
        )
        
        # Update session state with edits
        st.session_state.project_data = edited_df

    # --- TAB 2: CALCULATIONS ---
    with tab2:
        # PERFORM CALCULATIONS ON EDITED_DF
        calc_df = edited_df.copy()
        
        # 1. Material Properties
        fck = int(conc_grade[1:])
        sigma_cbc = get_sigma_cbc(conc_grade)
        m_ratio = 280 / (3 * sigma_cbc)
        E_static = 5700 * np.sqrt(fck) # N/mm2 = MN/m2
        # Convert Es to t/m2 (approx 1 N/mm2 = 101.97 t/m2)
        E_static_tm2 = E_static * 101.97
        
        st.markdown(f"**Material Constants:** `Fck`: {fck} | `m`: {round(m_ratio, 2)} | `Es`: {E_static_tm2:,.2f} t/m2")
        
        # 2. Section Properties (Area, Inertia)
        calc_df['Area (m2)'] = (np.pi / 4) * (calc_df['Outer Dia (m)']**2 - calc_df['Inner Dia (m)']**2)
        calc_df['Inertia (m4)'] = (np.pi / 64) * (calc_df['Outer Dia (m)']**4 - calc_df['Inner Dia (m)']**4)
        
        # 3. Dead Load Calculation (Self Weight)
        # We need to look ahead to the next row to form a segment
        # Shift the dataframe to get "Next Level" properties
        calc_df['Next Level'] = calc_df['Level (m)'].shift(-1)
        calc_df['Next OD'] = calc_df['Outer Dia (m)'].shift(-1)
        calc_df['Next ID'] = calc_df['Inner Dia (m)'].shift(-1)
        
        weights = []
        for index, row in calc_df.iterrows():
            if pd.isna(row['Next Level']):
                weights.append(0.0) # Bottom-most level (Raft) has no weight below it
            else:
                h = row['Level (m)'] - row['Next Level']
                r1_out = row['Outer Dia (m)'] / 2
                r1_in = row['Inner Dia (m)'] / 2
                r2_out = row['Next OD'] / 2
                r2_in = row['Next ID'] / 2
                
                # Use average density of this segment
                vol = calculate_frustum_volume(h, r1_out, r1_in, r2_out, r2_in)
                wt = vol * row['Density (t/m3)']
                weights.append(wt)
        
        calc_df['Shell Weight (t)'] = weights
        
        # 4. Total Load per Level
        calc_df['Total Weight Segment (t)'] = (
            calc_df['Shell Weight (t)'] + 
            calc_df['Platform Load (t)'] + 
            calc_df['Liner Load (t)'] + 
            calc_df['Corbel Load (t)']
        )
        
        # 5. Cumulative Load (Top Down)
        # Since row 0 is top, we use cumsum
        calc_df['Cumulative Load (t)'] = calc_df['Total Weight Segment (t)'].cumsum()
        
        # Clean up display
        display_cols = [
            'Level (m)', 'Outer Dia (m)', 'Inner Dia (m)', 'Thickness (m)', 
            'Area (m2)', 'Inertia (m4)', 
            'Shell Weight (t)', 'Platform Load (t)', 'Liner Load (t)', 
            'Cumulative Load (t)'
        ]
        
        st.dataframe(calc_df[display_cols].style.format("{:.3f}"), height=600)
        
        # Visualization
        st.subheader("Load Distribution Profile")
        st.line_chart(calc_df, x='Level (m)', y='Cumulative Load (t)')

    # --- TAB 3: SAVE/EXPORT ---
    with tab3:
        st.header("Download Project")
        
        # Prepare JSON
        project_export = {
            "meta": {
                "height": total_height,
                "grade": conc_grade
            },
            "grid": edited_df.to_dict(orient='records')
        }
        
        json_str = json.dumps(project_export, indent=4)
        
        st.download_button(
            label="💾 Download Project File (.json)",
            data=json_str,
            file_name="chimney_project.json",
            mime="application/json"
        )
        
        st.divider()
        
        # Export Results to CSV
        csv = calc_df.to_csv(index=False)
        st.download_button(
            label="📊 Export Calculation Table to Excel/CSV",
            data=csv,
            file_name="chimney_calculations.csv",
            mime="text/csv"
        )

else:
    st.info("👈 Please enter parameters in the Sidebar and click 'Generate/Reset Grid' to start.")
