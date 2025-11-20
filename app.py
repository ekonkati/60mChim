import streamlit as st
import pandas as pd
import numpy as np

# --- CONFIGURATION ---
st.set_page_config(page_title="Chimney Design Workbook (IS:4998)", layout="wide")

# --- SESSION STATE (The Workbook Memory) ---
if 'workbook_data' not in st.session_state:
    st.session_state.workbook_data = None
    
# --- DEFAULT INPUTS (From your file) ---
if 'params' not in st.session_state:
    st.session_state.params = {
        'total_height': 30.0,
        'top_inner_dia': 1.35,
        'thickness': 0.200,
        'conc_density': 2.5,
        'grade_conc': 'M30',
        'wind_speed': 47.0, # Typical Zone 4
        'seismic_zone': 0.16 # Zone III
    }

# ==============================================================================
# 1. SHEET 1: DEAD LOADS LOGIC (Replicating your file exactly)
# ==============================================================================
def generate_sheet_1(params):
    # Hardcoded levels from your file to match the specific segmentation
    levels = [30.3, 30.0, 27.5, 25.0, 22.5, 20.0, 17.5, 15.0, 12.5, 10.0, 7.5, 5.0, 2.5, 0.0, -1.7, -3.0]
    
    data = []
    
    for i, lvl in enumerate(levels):
        # Geometry Logic
        inner_dia = params['top_inner_dia'] 
        outer_dia = inner_dia + (2 * params['thickness'])
        
        # Area & Inertia
        area = (np.pi / 4) * (outer_dia**2 - inner_dia**2)
        inertia = (np.pi / 64) * (outer_dia**4 - inner_dia**4)
        z_mod = inertia / (outer_dia / 2)
        
        # Height of Segment Calculation
        height_segment = 0.0
        if i < len(levels) - 1:
            height_segment = lvl - levels[i+1]
            
        # Manual Fix for Top Row to match your file
        if lvl == 30.3: height_segment = 0.0
        if lvl == 30.0: height_segment = 0.3 # 30.3 - 30.0
        
        # Shell Weight Calculation
        shell_wt = area * height_segment * params['conc_density']
        
        data.append({
            'Level': lvl,
            'Segment_H': height_segment,
            'Outer_Dia': outer_dia,
            'Inner_Dia': inner_dia,
            'Thickness': params['thickness'],  # <--- FIXED: Added this missing key
            'Area': area,
            'Inertia': inertia,
            'Z_Modulus': z_mod,
            'Shell_Wt': shell_wt,
            # Placeholders for manual inputs found in your file
            'Liner_Load': 0.0,
            'Platform_Load': 0.0,
            'Corbel_Load': 0.0
        })
        
    return pd.DataFrame(data)

# ==============================================================================
# 2. SHEET 2: WIND LOADS LOGIC (IS:4998 / IS:875)
# ==============================================================================
def calculate_sheet_2(df, vb, k1=1.0, k3=1.0, cd=0.8):
    # Logic: Vz = Vb * k1 * k2 * k3
    # Force = 0.6 * Vz^2 * Projected Area * Cd
    
    # Simplified k2 table (Terrain Category 2)
    def get_k2(h):
        if h <= 10: return 1.0
        if h <= 15: return 1.05
        if h <= 20: return 1.07
        if h <= 30: return 1.12
        return 1.15

    wind_forces = []
    for i, row in df.iterrows():
        # Calculate at Mid-height of segment
        h_calc = row['Level'] if row['Level'] > 0 else 0
        k2 = get_k2(h_calc)
        vz = vb * k1 * k2 * k3
        pz = 0.6 * (vz**2) / 1000 # kN/m2
        
        projected_area = row['Outer_Dia'] * row['Segment_H']
        force_kn = pz * projected_area * cd
        force_ton = force_kn / 9.81 # Convert kN to Ton
        
        wind_forces.append(force_ton)
        
    df['Wind_Force_Ton'] = wind_forces
    
    # Cumulative Shear (Top -> Down)
    df['Wind_Shear'] = df['Wind_Force_Ton'].cumsum()
    
    # Cumulative Moment (Cantilever method)
    # M_i = M_(i-1) + Shear_(i-1) * h
    moments = [0.0] * len(df)
    for i in range(1, len(df)):
        moments[i] = moments[i-1] + (df.at[i-1, 'Wind_Shear'] * df.at[i-1, 'Segment_H'])
    
    df['Wind_Moment'] = moments
    return df

# ==============================================================================
# 3. SHEET 3: SEISMIC LOADS LOGIC (IS:1893 / IS:4998)
# ==============================================================================
def calculate_sheet_3(df, zone_factor, I=1.5, R=3.0, Sa_g=2.5):
    # 1. Calculate Total Mass (Seismic Weight)
    # W = Shell + Liner + Platform + Corbel
    df['Total_Node_Wt'] = df['Shell_Wt'] + df['Liner_Load'] + df['Platform_Load'] + df['Corbel_Load']
    total_weight = df['Total_Node_Wt'].sum()
    
    # 2. Base Shear (Ah * W)
    Ah = (zone_factor / 2) * (I / R) * Sa_g
    Base_Shear = Ah * total_weight
    
    # 3. Distribution (Qi = VB * (Wi * hi^2) / Sum(Wi * hi^2))
    # Note: Level needs to be height above base. Your levels are relative to Raft.
    # If Raft is -3.0m, then Height h = Level - (-3.0)
    base_level = df['Level'].min()
    df['Height_h'] = df['Level'] - base_level
    
    df['Wi_hi2'] = df['Total_Node_Wt'] * (df['Height_h']**2)
    sum_Wi_hi2 = df['Wi_hi2'].sum()
    
    # Avoid division by zero if sum is 0
    if sum_Wi_hi2 == 0:
        df['Seismic_Force'] = 0
    else:
        df['Seismic_Force'] = Base_Shear * (df['Wi_hi2'] / sum_Wi_hi2)
    
    # 4. Shear & Moment
    df['Seismic_Shear'] = df['Seismic_Force'].cumsum() # Approx top-down for simplified check
    
    moments = [0.0] * len(df)
    for i in range(1, len(df)):
        moments[i] = moments[i-1] + (df.at[i-1, 'Seismic_Shear'] * df.at[i-1, 'Segment_H'])
    
    df['Seismic_Moment'] = moments
    return df, Base_Shear

# ==============================================================================
# 4. SHEET 4: STRESS ANALYSIS (P/A +/- M/Z)
# ==============================================================================
def calculate_sheet_4(df):
    # Total Cumulative Vertical Load (P)
    df['Axial_Load_P'] = df['Total_Node_Wt'].cumsum()
    
    # Governing Moment (Max of Wind or Seismic)
    df['Design_Moment_M'] = df[['Wind_Moment', 'Seismic_Moment']].max(axis=1)
    
    # Stress Calc
    # Sigma = P/A +/- M/Z
    results = []
    for i, row in df.iterrows():
        P = row['Axial_Load_P']
        M = row['Design_Moment_M']
        A = row['Area']
        Z = row['Z_Modulus']
        
        sigma_direct = P / A if A > 0 else 0
        sigma_bending = M / Z if Z > 0 else 0
        
        max_comp = sigma_direct + sigma_bending
        min_stress = sigma_direct - sigma_bending # If negative, it's Tension
        
        results.append({
            'Level': row['Level'],
            'Axial_P': P,
            'Moment_M': M,
            'Stress_Direct': sigma_direct,
            'Stress_Bending': sigma_bending,
            'Max_Comp (t/m2)': max_comp,
            'Min_Stress (t/m2)': min_stress,
            'Status': "⚠️ TENSION" if min_stress < 0 else "OK"
        })
        
    return pd.DataFrame(results)

# ==============================================================================
# MAIN APP INTERFACE
# ==============================================================================

st.title("🏭 RC Chimney Analysis Workbook")

# 1. Sidebar Inputs
with st.sidebar:
    st.header("Global Parameters")
    p = st.session_state.params
    p['total_height'] = st.number_input("Total Height (m)", value=p['total_height'])
    p['top_inner_dia'] = st.number_input("Top Inner Dia (m)", value=p['top_inner_dia'])
    p['thickness'] = st.number_input("Shell Thickness (m)", value=p['thickness'])
    
    st.markdown("---")
    st.header("Load Parameters")
    p['wind_speed'] = st.number_input("Basic Wind Speed (m/s)", value=p['wind_speed'])
    p['seismic_zone'] = st.number_input("Seismic Zone Factor (Z)", value=p['seismic_zone'])
    
    if st.button("🔄 Reset / Generate"):
        st.session_state.workbook_data = generate_sheet_1(p)
        st.rerun()

# Initialize if needed
if st.session_state.workbook_data is None:
    st.session_state.workbook_data = generate_sheet_1(st.session_state.params)

df_main = st.session_state.workbook_data

# 2. TABS
tab1, tab2, tab3, tab4 = st.tabs([
    "1. Dead Loads (Geometry)", 
    "2. Wind Loads", 
    "3. Seismic Loads", 
    "4. Stress Results"
])

# --- TAB 1: DEAD LOADS ---
with tab1:
    st.subheader("I. DEAD LOADS & GEOMETRY")
    st.info("Standard weights (Shell) are calculated. Please update 'Liner' and 'Platform' loads manually to match your architectural drawings.")
    
    # Editable Grid
    cols = ['Level', 'Outer_Dia', 'Inner_Dia', 'Thickness', 'Shell_Wt', 'Liner_Load', 'Platform_Load', 'Corbel_Load']
    
    edited_df = st.data_editor(
        df_main[cols], 
        height=500, 
        use_container_width=True,
        column_config={
            "Shell_Wt": st.column_config.NumberColumn(disabled=True, format="%.3f"),
            "Outer_Dia": st.column_config.NumberColumn(format="%.3f"),
            "Level": st.column_config.NumberColumn(format="%.3f"),
            "Thickness": st.column_config.NumberColumn(format="%.3f")
        }
    )
    
    # Save edits
    df_main.update(edited_df)
    st.session_state.workbook_data = df_main

# --- TAB 2: WIND LOADS ---
with tab2:
    st.subheader("II. WIND LOAD ANALYSIS")
    
    # Recalculate Wind
    df_wind = calculate_sheet_2(df_main.copy(), vb=p['wind_speed'])
    st.session_state.workbook_data = df_wind # Merge results back
    
    st.dataframe(
        df_wind[['Level', 'Wind_Force_Ton', 'Wind_Shear', 'Wind_Moment']].style.format("{:.3f}"), 
        use_container_width=True
    )
    st.line_chart(df_wind.set_index('Level')[['Wind_Moment']])

# --- TAB 3: SEISMIC LOADS ---
with tab3:
    st.subheader("III. SEISMIC LOAD ANALYSIS")
    
    # Recalculate Seismic
    df_seismic, base_shear = calculate_sheet_3(st.session_state.workbook_data.copy(), zone_factor=p['seismic_zone'])
    st.session_state.workbook_data = df_seismic # Merge results back
    
    st.metric("Total Base Shear (Vb)", f"{base_shear:.2f} Ton")
    
    st.dataframe(
        df_seismic[['Level', 'Total_Node_Wt', 'Seismic_Force', 'Seismic_Shear', 'Seismic_Moment']].style.format("{:.3f}"),
        use_container_width=True
    )
    st.line_chart(df_seismic.set_index('Level')[['Seismic_Moment']])

# --- TAB 4: STRESS ANALYSIS ---
with tab4:
    st.subheader("IV. RESULTANT STRESSES")
    
    df_stress = calculate_sheet_4(st.session_state.workbook_data.copy())
    
    # Formatting: Highlight Tension in Red
    def highlight_tension(val):
        color = 'red' if val < 0 else 'green'
        return f'color: {color}; font-weight: bold'

    st.dataframe(
        df_stress.style.format("{:.2f}").applymap(highlight_tension, subset=['Min_Stress (t/m2)']),
        use_container_width=True,
        height=600
    )
    
    # CSV Download
    csv = df_stress.to_csv(index=False).encode('utf-8')
    st.download_button("Download Full Calculation Report", csv, "chimney_report.csv", "text/csv")
