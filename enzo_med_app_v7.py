import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime, date, timedelta
import os
import time

# --- CONFIGURATION DE LA PAGE ---
st.set_page_config(
    page_title="Enzo-Med | Planificateur Actif de Révisions",
    page_icon="🩺",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- STYLE CSS PERSONNALISÉ ---
st.markdown("""
<style>
    .main-title {
        font-size: 2.8rem;
        font-weight: 800;
        color: #1E3A8A;
        margin-bottom: 0.5rem;
    }
    .subtitle {
        font-size: 1.2rem;
        color: #4B5563;
        margin-bottom: 2rem;
    }
    .card-kpi {
        background-color: #F3F4F6;
        padding: 1.5rem;
        border-radius: 0.75rem;
        border-left: 5px solid #3B82F6;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
    }
    .feedback-positive {
        color: #10B981;
        font-weight: bold;
    }
    .feedback-negative {
        color: #EF4444;
        font-weight: bold;
    }
    .objective-box {
        background-color: #F9FAFB;
        border-left: 4px solid #1E3A8A;
        padding: 10px;
        border-radius: 4px;
        margin-bottom: 10px;
    }
</style>
""", unsafe_allow_html=True)

# --- INITIALISATION DE LA BASE DE DONNÉES (SQLite) ---
DB_PATH = "enzo_med_database.db"

# Définition des objectifs et minuteurs par défaut selon la méthode d'Enzo
DEFAULT_OBJECTIVES = {
    "50% (Bases & Structure)": [
        "Définition de base & Physiopathologie simple (le mécanisme clé)",
        "Signes cliniques cardinaux (les maîtres-symptômes)",
        "Diagnostic positif évident (les examens clés de première intention)"
    ],
    "60% (Détails majeurs)": [
        "Examens complémentaires complets (résultats attendus)",
        "Critères diagnostiques officiels (scores cliniques et classifications)",
        "Complications majeures à redouter absolument",
        "Prise en charge thérapeutique de première ligne (grandes classes)"
    ],
    "80% (Maîtrise & QCM)": [
        "Diagnostics différentiels majeurs (les pièges à éliminer)",
        "Contre-indications thérapeutiques absolues",
        "Formes cliniques particulières (enfant, sujet âgé, urgence extrême)",
        "Détails fins du cours & 'Kollas' récurrents en QCM"
    ],
    "100% (Parfait)": [
        "Cas cliniques complexes & transversaux d'annales",
        "Chiffres précis indispensables (épidémiologie, prévalence, seuils biologiques)",
        "Recommandations de dernière minute (HAS, consensus internationaux)"
    ]
}

DEFAULT_TIMERS = {
    "50% (Bases & Structure)": 2,
    "60% (Détails majeurs)": 3,
    "80% (Maîtrise & QCM)": 5,
    "100% (Parfait)": 7
}

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # Table des matières (UE)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS subjects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        )
    """)
    # Table des chapitres
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chapters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ue TEXT NOT NULL,
            name TEXT NOT NULL,
            date_j0 DATE NOT NULL,
            target_palier TEXT NOT NULL,
            current_interval INTEGER NOT NULL,
            next_revision DATE NOT NULL,
            last_test_date DATE,
            last_test_result TEXT,
            test_method TEXT NOT NULL
        )
    """)
    # Table d'historique des tests pour les statistiques
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS test_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chapter_id INTEGER,
            test_date DATE NOT NULL,
            result TEXT NOT NULL,
            interval_applied INTEGER,
            FOREIGN KEY (chapter_id) REFERENCES chapters(id)
        )
    """)
    
    # Evolution de la structure de base (Migrations de colonnes invisibles pour l'utilisateur)
    try:
        cursor.execute("ALTER TABLE chapters ADD COLUMN objectives TEXT")
    except sqlite3.OperationalError:
        pass  # La colonne existe déjà
    try:
        cursor.execute("ALTER TABLE chapters ADD COLUMN timer_duration INTEGER DEFAULT 2")
    except sqlite3.OperationalError:
        pass  # La colonne existe déjà
        
    # Insérer les matières par défaut si la table est vide
    cursor.execute("SELECT COUNT(*) FROM subjects")
    if cursor.fetchone()[0] == 0:
        default_subjects = [
            "UE 1 - Chimie / Biochimie",
            "UE 2 - Biologie Cellulaire",
            "UE 3 - Biophysique",
            "UE 4 - Biostatistiques",
            "UE 5 - Anatomie",
            "UE 6 - Initiation Connaissance Médicament",
            "UE 7 - Santé, Société, Humanité"
        ]
        for sub in default_subjects:
            cursor.execute("INSERT OR IGNORE INTO subjects (name) VALUES (?)", (sub,))
            
    conn.commit()
    conn.close()

init_db()

# --- FONCTIONS DE GESTION DE LA BASE DE DONNÉES ---
def get_connection():
    return sqlite3.connect(DB_PATH)

def get_all_subjects():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM subjects ORDER BY name ASC")
    subjects = [row[0] for row in cursor.fetchall()]
    conn.close()
    return subjects

def add_subject(subject_name):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO subjects (name) VALUES (?)", (subject_name.strip(),))
        conn.commit()
        success = True
    except sqlite3.IntegrityError:
        success = False
    conn.close()
    return success

def delete_subject(subject_name):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM subjects WHERE name = ?", (subject_name,))
    conn.commit()
    conn.close()

def add_chapter(ue, name, date_j0, target_palier, current_interval, test_method, objectives, timer_duration):
    conn = get_connection()
    cursor = conn.cursor()
    
    # Gestion de la détection et écrasement automatique des anciens paliers du même cours
    cursor.execute("SELECT id FROM chapters WHERE ue = ? AND name = ?", (ue, name))
    existing_rows = cursor.fetchall()
    for row in existing_rows:
        old_id = row[0]
        cursor.execute("DELETE FROM chapters WHERE id = ?", (old_id,))
        cursor.execute("DELETE FROM test_history WHERE chapter_id = ?", (old_id,))
        
    next_rev = datetime.strptime(str(date_j0), "%Y-%m-%d").date() + timedelta(days=current_interval)
    cursor.execute("""
        INSERT INTO chapters (ue, name, date_j0, target_palier, current_interval, next_revision, test_method, objectives, timer_duration)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (ue, name, date_j0, target_palier, current_interval, next_rev, test_method, objectives, timer_duration))
    conn.commit()
    conn.close()

def log_test_consolidation(chapter_id):
    conn = get_connection()
    cursor = conn.cursor()
    today = date.today()
    cursor.execute("SELECT current_interval, name, target_palier FROM chapters WHERE id = ?", (int(chapter_id),))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return
    current_interval, name, target_palier = row
    
    new_interval = max(1, int(current_interval * 1.5))
    next_rev = today + timedelta(days=new_interval)
    
    cursor.execute("""
        UPDATE chapters
        SET current_interval = ?, next_revision = ?, last_test_date = ?, last_test_result = ?
        WHERE id = ?
    """, (new_interval, next_rev, today, f"Réussi - Consolidé ({target_palier})", int(chapter_id)))
    
    cursor.execute("""
        INSERT INTO test_history (chapter_id, test_date, result, interval_applied)
        VALUES (?, ?, ?, ?)
    """, (int(chapter_id), today, f"Réussi - Consolidé ({target_palier})", current_interval))
    
    conn.commit()
    conn.close()
    st.success(f"Niveau consolidé avec succès pour : **{name}** ! Prochain test planifié à J+{new_interval}.")

def transition_to_higher_palier(chapter_id):
    conn = get_connection()
    cursor = conn.cursor()
    today = date.today()
    cursor.execute("SELECT target_palier, current_interval, name, ue, test_method FROM chapters WHERE id = ?", (int(chapter_id),))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return
    current_palier, current_interval, name, ue, test_method = row
    
    # Évolution des paliers
    progression = {
        "50% (Bases & Structure)": "60% (Détails majeurs)",
        "60% (Détails majeurs)": "80% (Maîtrise & QCM)",
        "80% (Maîtrise & QCM)": "100% (Parfait)",
        "100% (Parfait)": "100% (Parfait)"
    }
    
    new_palier = progression.get(current_palier, "100% (Parfait)")
    
    # Nouveaux objectifs de médecine par défaut
    new_objectives_list = DEFAULT_OBJECTIVES.get(new_palier, ["Révision générale approfondie"])
    new_objectives_str = "\n".join([f"☐ {obj}" for obj in new_objectives_list])
    
    # Nouveau minuteur par défaut adapté
    new_timer = DEFAULT_TIMERS.get(new_palier, 5)
    
    # Calcul espacement
    new_interval = max(1, int(current_interval * 1.5))
    next_rev = today + timedelta(days=new_interval)
    
    cursor.execute("""
        UPDATE chapters
        SET target_palier = ?, current_interval = ?, next_revision = ?, last_test_date = ?, last_test_result = ?, objectives = ?, timer_duration = ?
        WHERE id = ?
    """, (new_palier, new_interval, next_rev, today, f"Réussi ➡️ Palier Supérieur ({new_palier})", new_objectives_str, new_timer, int(chapter_id)))
    
    cursor.execute("""
        INSERT INTO test_history (chapter_id, test_date, result, interval_applied)
        VALUES (?, ?, ?, ?)
    """, (int(chapter_id), today, f"Réussi ➡️ Palier Supérieur ({new_palier})", current_interval))
    
    conn.commit()
    conn.close()
    st.balloons()
    st.success(f"Bravo ! Le cours **{name}** a été promu au palier **{new_palier}** ! Prochaine révision active le {next_rev}.")

def log_test_failure(chapter_id):
    conn = get_connection()
    cursor = conn.cursor()
    today = date.today()
    cursor.execute("SELECT current_interval, name, target_palier FROM chapters WHERE id = ?", (int(chapter_id),))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return
    current_interval, name, target_palier = row
    
    # Division par 2 de l'intervalle de rétention, révision d'urgence dès demain (J+1)
    new_interval = max(1, int(current_interval / 2))
    next_rev = today + timedelta(days=1)
    
    cursor.execute("""
        UPDATE chapters
        SET current_interval = ?, next_revision = ?, last_test_date = ?, last_test_result = ?
        WHERE id = ?
    """, (new_interval, next_rev, today, f"Échoué - Feedback Négatif ({target_palier})", int(chapter_id)))
    
    cursor.execute("""
        INSERT INTO test_history (chapter_id, test_date, result, interval_applied)
        VALUES (?, ?, ?, ?)
    """, (int(chapter_id), today, f"Échoué - Feedback Négatif ({target_palier})", current_interval))
    
    conn.commit()
    conn.close()
    st.error(f"Alerte oubli sur **{name}** ! Planifié en urgence pour demain afin de fixer les idées. 🚨")

def delete_chapter(chapter_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM chapters WHERE id = ?", (int(chapter_id),))
    cursor.execute("DELETE FROM test_history WHERE chapter_id = ?", (int(chapter_id),))
    conn.commit()
    conn.close()

# --- CONSTRUCTEUR DE MINUTEUR INDÉPENDANT PAR CARTE (HTML/JS) ---
def render_adaptive_timer(duration_minutes, key):
    timer_id = f"timer_{key}"
    seconds_total = duration_minutes * 60
    timer_html = f"""
    <div id="container_{timer_id}" style="text-align: center; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; padding: 12px; background-color: #F3F4F6; border-radius: 8px; border: 1px solid #D1D5DB; margin: 10px 0;">
        <h2 id="{timer_id}" style="font-size: 2.8rem; font-weight: bold; color: #1E3A8A; margin: 5px 0;">{duration_minutes:02d}:00</h2>
        <div style="margin-top: 8px;">
            <button id="start_{timer_id}" onclick="startTimer_{timer_id}()" style="background-color: #3B82F6; color: white; border: none; padding: 8px 15px; font-size: 0.95rem; border-radius: 5px; cursor: pointer; margin-right: 5px; font-weight: 600;">Démarrer</button>
            <button id="stop_{timer_id}" onclick="stopTimer_{timer_id}()" style="background-color: #EF4444; color: white; border: none; padding: 8px 15px; font-size: 0.95rem; border-radius: 5px; cursor: pointer; margin-right: 5px; font-weight: 600;">Pause</button>
            <button id="reset_{timer_id}" onclick="resetTimer_{timer_id}()" style="background-color: #6B7280; color: white; border: none; padding: 8px 15px; font-size: 0.95rem; border-radius: 5px; cursor: pointer; font-weight: 600;">Réinitialiser</button>
        </div>
    </div>

    <script>
        var timeLeft_{timer_id} = {seconds_total};
        var timerInterval_{timer_id};
        var running_{timer_id} = false;

        function updateDisplay_{timer_id}() {{
            var minutes = Math.floor(timeLeft_{timer_id} / 60);
            var seconds = timeLeft_{timer_id} % 60;
            if (seconds < 10) seconds = "0" + seconds;
            if (minutes < 10) minutes = "0" + minutes;
            document.getElementById("{timer_id}").innerHTML = minutes + ":" + seconds;
        }}

        function startTimer_{timer_id}() {{
            if (running_{timer_id}) return;
            running_{timer_id} = true;
            timerInterval_{timer_id} = setInterval(function() {{
                if(timeLeft_{timer_id} <= 0) {{
                    clearInterval(timerInterval_{timer_id});
                    document.getElementById("{timer_id}").innerHTML = "TEMPS ÉCOULÉ !";
                    document.getElementById("{timer_id}").style.color = "#EF4444";
                    running_{timer_id} = false;
                    
                    // Alerte sonore intégrée via l'AudioContext natif du navigateur
                    try {{
                        var context = new (window.AudioContext || window.webkitAudioContext)();
                        var oscillator = context.createOscillator();
                        var gainNode = context.createGain();
                        oscillator.connect(gainNode);
                        gainNode.connect(context.destination);
                        oscillator.type = 'sine';
                        oscillator.frequency.setValueAtTime(587.33, context.currentTime); // Ré5
                        gainNode.gain.setValueAtTime(0.3, context.currentTime);
                        oscillator.start();
                        setTimeout(function() {{ oscillator.stop(); }}, 1000);
                    }} catch(e) {{
                        console.log("Audio non supporté ou bloqué par le navigateur");
                    }}
                }} else {{
                    timeLeft_{timer_id}--;
                    updateDisplay_{timer_id}();
                }}
            }}, 1000);
        }}

        function stopTimer_{timer_id}() {{
            clearInterval(timerInterval_{timer_id});
            running_{timer_id} = false;
        }}

        function resetTimer_{timer_id}() {{
            clearInterval(timerInterval_{timer_id});
            timeLeft_{timer_id} = {seconds_total};
            running_{timer_id} = false;
            document.getElementById("{timer_id}").style.color = "#1E3A8A";
            updateDisplay_{timer_id}();
        }}
    </script>
    """
    st.components.v1.html(timer_html, height=135)

# Header de l'application
st.markdown('<div class="main-title">🩺 Enzo-Med</div>', unsafe_allow_html=True)
st.markdown('<div class="subtitle">Pilotez intelligemment vos révisions de médecine avec la méthode d\'Enzo (Feedback Actif et Espacement Dynamique)</div>', unsafe_allow_html=True)

# Charger la liste des matières dynamiquement
subjects_list = get_all_subjects()

# --- BARRE LATÉRALE ---
with st.sidebar:
    st.image("https://images.unsplash.com/photo-1576091160550-2173dba999ef?q=80&w=250", use_container_width=True)
    
    # Formulaire 1 : Ajouter un chapitre
    st.header("📝 Nouveau Chapitre")
    with st.form("add_chapter_form", clear_on_submit=True):
        ue = st.selectbox("UE / Matière", subjects_list)
        name = st.text_input("Nom du Chapitre", placeholder="Ex: Cycle de Krebs, Tissu Épithélial...")
        date_j0 = st.date_input("Date du premier passage (J0)", date.today())
        
        target_palier = st.selectbox("Palier de connaissances ciblé", [
            "50% (Bases & Structure)", 
            "60% (Détails majeurs)", 
            "80% (Maîtrise & QCM)", 
            "100% (Parfait)"
        ])
        
        init_interval = st.number_input("Intervalle Initial (jours)", min_value=1, max_value=30, value=3)
        test_method = st.selectbox("Méthode de Test Préférée", [
            "Feuille Blanche (2 min)", 
            "Méthode Feynman à l'oral", 
            "Session de QCM ciblés", 
            "Flashcards (Détails précis)"
        ])
        
        # Charger les valeurs recommandées par défaut pour aider l'étudiant
        default_objs_list = DEFAULT_OBJECTIVES.get(target_palier, [])
        default_objs_str = "\n".join([f"☐ {obj}" for obj in default_objs_list])
        default_timer = DEFAULT_TIMERS.get(target_palier, 2)
        
        st.markdown("---")
        st.markdown("💡 **Ajustement de la difficulté :**")
        
        objectives_input = st.text_area(
            "Critères de validation à restituer de mémoire",
            value=default_objs_str,
            height=120,
            help="Saisissez vos propres objectifs de médecine si besoin (un objectif par ligne)."
        )
        
        timer_duration = st.slider(
            "Timer recommandé (minutes)",
            min_value=1,
            max_value=15,
            value=default_timer,
            help="Ajusté automatiquement selon le palier, modifiable à votre guise."
        )
        
        submit_btn = st.form_submit_button("Ajouter à mon planning")
        if submit_btn:
            if name.strip() == "":
                st.error("Le nom du chapitre ne peut pas être vide.")
            else:
                add_chapter(ue, name, date_j0, target_palier, init_interval, test_method, objectives_input, timer_duration)
                st.success(f"Chapitre '{name}' planifié avec succès !")
                time.sleep(0.5)
                st.rerun()
                
    # Formulaire 2 : Ajouter une nouvelle matière
    st.markdown("---")
    st.header("➕ Ajouter une Matière / UE")
    with st.form("add_subject_form", clear_on_submit=True):
        new_sub_name = st.text_input("Nom de la Matière (ex: UE 8 - Spécialité)")
        sub_submit_btn = st.form_submit_button("Créer la matière")
        if sub_submit_btn:
            if new_sub_name.strip() == "":
                st.error("Le nom de la matière ne peut pas être vide.")
            else:
                if add_subject(new_sub_name):
                    st.success(f"Matière '{new_sub_name}' ajoutée !")
                    time.sleep(0.5)
                    st.rerun()
                else:
                    st.error("Cette matière existe déjà !")

# --- RÉCUPÉRATION DES DONNÉES ---
conn = get_connection()
df_chapters = pd.read_sql_query("SELECT * FROM chapters", conn)
df_history = pd.read_sql_query("SELECT * FROM test_history", conn)
conn.close()

today = date.today()

# Conversion des colonnes de dates en vrais objets Date
if not df_chapters.empty:
    df_chapters['date_j0'] = pd.to_datetime(df_chapters['date_j0']).dt.date
    df_chapters['next_revision'] = pd.to_datetime(df_chapters['next_revision']).dt.date
    df_chapters['last_test_date'] = pd.to_datetime(df_chapters['last_test_date']).dt.date
    
    # Calcul des indicateurs de délai à la volée
    df_chapters['days_remaining'] = df_chapters['next_revision'].apply(lambda x: (x - today).days)
    
    # Détermination du Statut
    def get_status(days):
        if days < 0:
            return "🚨 EN RETARD"
        elif days == 0:
            return "📅 À FAIRE AUJOURD'HUI"
        else:
            return "⏳ Planifié"
            
    df_chapters['status'] = df_chapters['days_remaining'].apply(get_status)

# --- PARTIE 1 : TABLEAU DE BORD (KPIs) ---
st.header("📊 Tableau de Bord Personnel")
col1, col2, col3, col4 = st.columns(4)

if df_chapters.empty:
    with col1:
        st.metric("Total Chapitres", 0)
    with col2:
        st.metric("Urgences de Révision", 0)
    with col3:
        st.metric("Taux de Réussite Global", "0%")
    with col4:
        st.metric("Intervalle Moyen", "0 jours")
else:
    total_caps = len(df_chapters)
    urgencies = len(df_chapters[df_chapters['days_remaining'] <= 0])
    
    # Calcul taux de réussite complet (promotions + consolidations vs échecs)
    tests_reussis = len(df_history[df_history['result'].str.contains("Réussi", na=False)])
    tests_totaux = len(df_history)
    success_rate = f"{int((tests_reussis / tests_totaux) * 100)}%" if tests_totaux > 0 else "N/A"
    
    avg_interval = f"{df_chapters['current_interval'].mean():.1f} jours"
    
    with col1:
        st.metric("Total Chapitres Suivis", total_caps)
    with col2:
        st.metric("À Réviser d'Urgence", urgencies, delta=-urgencies, delta_color="inverse")
    with col3:
        st.metric("Taux de Réussite aux Tests", success_rate)
    with col4:
        st.metric("Intervalle Moyen de Rétention", avg_interval)

st.markdown("---")

# --- PARTIE 2 : ONGLETS D'ACTION ---
tab_today, tab_all, tab_timer, tab_stats = st.tabs([
    "📅 Révisions du Jour", 
    "📚 Tous mes Chapitres", 
    "⏱️ Timer Récupération Éclair", 
    "📈 Analyse & Statistiques"
])

# --- ONGLET 1 : RÉVISIONS DU JOUR ---
with tab_today:
    st.subheader("🔥 Vos priorités d'aujourd'hui")
    if df_chapters.empty:
        st.info("Ajoutez des chapitres dans la barre latérale pour commencer votre planification.")
    else:
        # Filtrer pour obtenir uniquement les chapitres à réviser aujourd'hui ou en retard
        df_today = df_chapters[df_chapters['days_remaining'] <= 0].sort_values(by="days_remaining")
        
        if df_today.empty:
            st.balloons()
            st.success("Félicitations ! Vous êtes totalement à jour pour aujourd'hui. Aucun chapitre en retard ! 🎉")
        else:
            st.warning(f"Vous avez **{len(df_today)}** chapitre(s) à tester ou réviser aujourd'hui.")
            
            for index, row in df_today.iterrows():
                with st.expander(f"{row['ue']} - **{row['name']}** (Palier actuel: {row['target_palier']} | Retard: {abs(row['days_remaining'])} j)", expanded=True):
                    col_info, col_act = st.columns([3, 2])
                    
                    with col_info:
                        st.markdown("##### 📋 Objectifs de récupération à tester :")
                        # Afficher la liste des objectifs sous forme de checklist interactive
                        if row['objectives']:
                            lines = [l.strip() for l in row['objectives'].split('\n') if l.strip()]
                            for i, line in enumerate(lines):
                                clean_line = line.lstrip("☐ ").lstrip("[ ] ")
                                st.checkbox(clean_line, key=f"obj_check_{row['id']}_{i}")
                        else:
                            st.write("*Aucun objectif précis saisi pour ce chapitre.*")
                            
                        st.markdown("---")
                        st.write(f"⏱️ **Méthode recommandée** : {row['test_method']}")
                        st.write(f"🔄 **Intervalle actuel** : {row['current_interval']} jours")
                        if row['last_test_date']:
                            st.write(f"📅 Dernier test : {row['last_test_date']} ({row['last_test_result']})")
                    
                    with col_act:
                        st.markdown("##### ⏱️ Timer Éclair Adaptatif")
                        # Utilisation du timer dynamique synchronisé avec la durée du chapitre
                        duration = row['timer_duration'] if row['timer_duration'] else 2
                        render_adaptive_timer(duration, f"act_{row['id']}")
                        
                        st.markdown("---")
                        st.markdown("##### 👉 Évaluation après le test :")
                        
                        # Choix triple selon la nouvelle dynamique d'Enzo
                        btn_col_neg, btn_col_cons, btn_col_prom = st.columns([1, 1.2, 1.3])
                        
                        with btn_col_neg:
                            if st.button("🔴 Échoué", key=f"btn_neg_{row['id']}", help="Oublis majeurs. Révision dès demain et intervalle réduit."):
                                log_test_failure(row['id'])
                                time.sleep(0.5)
                                st.rerun()
                                
                        with btn_col_cons:
                            if st.button("🔵 Consolider 🔒", key=f"btn_cons_{row['id']}", help="Réussi. Espacer la révision mais conserver ce palier."):
                                log_test_consolidation(row['id'])
                                time.sleep(0.5)
                                st.rerun()
                                
                        with btn_col_prom:
                            # Désactiver le bouton si on est déjà à 100%
                            is_max_level = row['target_palier'] == "100% (Parfait)"
                            btn_label = "🏆 Niveau Max !" if is_max_level else "🟢 Palier Sup ! 🚀"
                            if st.button(btn_label, key=f"btn_prom_{row['id']}", disabled=is_max_level, help="Maîtrisé. Passer automatiquement au palier suivant."):
                                transition_to_higher_palier(row['id'])
                                time.sleep(0.5)
                                st.rerun()

# --- ONGLET 2 : TOUS MES CHAPITRES ---
with tab_all:
    st.subheader("🗂️ Bibliothèque complète de vos chapitres")
    if df_chapters.empty:
        st.info("Votre bibliothèque est vide.")
    else:
        # Barre de recherche et filtre par UE
        col_search, col_filter = st.columns(2)
        with col_search:
            search_query = st.text_input("Rechercher un chapitre", "")
        with col_filter:
            filter_ue = st.selectbox("Filtrer par UE", ["Toutes"] + list(df_chapters['ue'].unique()))
            
        # Application des filtres
        df_filtered = df_chapters.copy()
        if search_query:
            df_filtered = df_filtered[df_filtered['name'].str.contains(search_query, case=False, na=False)]
        if filter_ue != "Toutes":
            df_filtered = df_filtered[df_filtered['ue'] == filter_ue]
            
        if df_filtered.empty:
            st.warning("Aucun chapitre ne correspond à vos critères de recherche.")
        else:
            # Structurer un affichage élégant sous forme de table propre
            display_cols = ["ue", "name", "target_palier", "current_interval", "timer_duration", "next_revision", "status", "last_test_result"]
            df_display = df_filtered[display_cols].rename(columns={
                "ue": "Matière / UE",
                "name": "Nom du Cours",
                "target_palier": "Objectif Palier",
                "current_interval": "Intervalle (jours)",
                "timer_duration": "Chrono (min)",
                "next_revision": "Prochaine Révision",
                "status": "Statut",
                "last_test_result": "Dernier Résultat"
            })
            
            st.dataframe(df_display, use_container_width=True, hide_index=True)
            
            # --- CONSOLE DE GESTION DE DONNÉES (TOUJOURS VISIBLE) ---
            st.markdown("---")
            st.markdown("### 🛠️ Console de Gestion Globale")
            
            col_del_ch, col_del_sub = st.columns(2)
            
            with col_del_ch:
                st.markdown("##### 🗑️ Supprimer un Chapitre")
                # Créer une liste de clés uniques "UE - Nom du Chapitre" pour lever toute ambiguïté
                df_filtered['select_label'] = df_filtered['ue'] + " : " + df_filtered['name']
                delete_option = st.selectbox("Sélectionner un cours à supprimer", df_filtered['select_label'].unique(), key="del_chapter_select")
                
                if st.button("Supprimer définitivement ce chapitre", key="del_chapter_btn"):
                    # Récupérer proprement la ligne correspondante
                    row_to_delete = df_filtered[df_filtered['select_label'] == delete_option]
                    if not row_to_delete.empty:
                        # Conversion explicite du ID numpy en int natif Python pour SQLite
                        target_id = int(row_to_delete['id'].values[0])
                        delete_chapter(target_id)
                        st.success("Le chapitre a bien été supprimé de la base de données ! Actualisation...")
                        time.sleep(0.5)
                        st.rerun()

            with col_del_sub:
                st.markdown("##### 🗑️ Supprimer une Matière (UE)")
                # Obtenir la liste de toutes les matières
                all_subs = get_all_subjects()
                delete_sub_option = st.selectbox("Sélectionner une matière à supprimer", all_subs, key="del_subject_select")
                
                if st.button("Supprimer définitivement cette matière", key="del_subject_btn"):
                    # Vérifier si des chapitres utilisent actuellement cette matière
                    conn = get_connection()
                    cursor = conn.cursor()
                    cursor.execute("SELECT COUNT(*) FROM chapters WHERE ue = ?", (delete_sub_option,))
                    chapters_count = cursor.fetchone()[0]
                    conn.close()
                    
                    if chapters_count > 0:
                        st.error(f"Impossible de supprimer '{delete_sub_option}' car {chapters_count} chapitre(s) y sont actuellement associés. Supprimez d'abord ces chapitres.")
                    else:
                        delete_subject(delete_sub_option)
                        st.success(f"La matière '{delete_sub_option}' a été supprimée avec succès ! Actualisation...")
                        time.sleep(0.5)
                        st.rerun()

# --- ONGLET 3 : TIMER RÉCUPÉRATION ÉCLAIR (AUTONOME) ---
with tab_timer:
    st.subheader("⏱️ Minuteur d'effort focalisé autonome")
    st.write("Fermez vos fiches, lancez le chronomètre pour un test rapide improvisé de la durée de votre choix !")
    
    # Un curseur pour régler le temps du timer autonome
    dur_autonome = st.slider("Régler la durée du test (minutes)", min_value=1, max_value=15, value=2, key="slider_autonome")
    render_adaptive_timer(dur_autonome, "autonome")
    st.info("💡 **Conseil d'Enzo :** Ce timer autonome est idéal si vous souhaitez tester à l'arrache un cours qui n'est pas programmé aujourd'hui dans votre agenda.")

# --- ONGLET 4 : ANALYSE & STATISTIQUES ---
with tab_stats:
    st.subheader("📈 Suivi scientifique de vos performances")
    if df_history.empty:
        st.info("L'historique est vide pour le moment. Effectuez vos premiers tests d'évaluation pour générer des statistiques.")
    else:
        col_chart1, col_chart2 = st.columns(2)
        
        with col_chart1:
            st.write("**Répartition globale de vos résultats de tests :**")
            # Nettoyer l'affichage des résultats pour des graphiques propres
            clean_results = df_history['result'].apply(lambda x: x.split('(')[0].strip())
            result_counts = clean_results.value_counts()
            st.bar_chart(result_counts)
            
        with col_chart2:
            st.write("**Évolution chronologique de vos révisions :**")
            # Grouper par date pour voir le nombre de tests passés par jour
            df_history['test_date'] = pd.to_datetime(df_history['test_date'])
            history_grouped = df_history.groupby('test_date').size().reset_index(name='Nombre de tests')
            history_grouped = history_grouped.set_index('test_date')
            st.line_chart(history_grouped)
            
        st.markdown("---")
        st.write("📊 **Détails de l'historique d'apprentissage :**")
        df_hist_display = df_history.copy()
        df_hist_display['test_date'] = pd.to_datetime(df_hist_display['test_date']).dt.date
        
        # Joindre pour avoir le nom du cours
        if not df_chapters.empty:
            df_hist_display = df_hist_display.merge(df_chapters[['id', 'name', 'ue']], left_on="chapter_id", right_on="id", how="left")
            st.dataframe(df_hist_display[['test_date', 'ue', 'name', 'result', 'interval_applied']].rename(columns={
                "test_date": "Date du Test",
                "ue": "UE",
                "name": "Nom du Cours",
                "result": "Résultat Enregistré",
                "interval_applied": "Intervalle de départ (jours)"
            }), use_container_width=True, hide_index=True)
