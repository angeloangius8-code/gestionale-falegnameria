import os
import sqlite3
import io
import re
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, send_file
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from PIL import Image as PILImage

# Tenta l'importazione sicura di psycopg2
try:
    import psycopg2
    from psycopg2.extras import DictCursor
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False

app = Flask(__name__)
app.secret_key = 'falegnameria_secret_key_secure'

# Rende la sessione temporanea: scade alla chiusura del browser
app.config['SESSION_PERMANENT'] = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_URL = os.environ.get('DATABASE_URL')

def get_db_connection():
    if DATABASE_URL and HAS_PSYCOPG2:
        # Se l'app gira su Render con PostgreSQL
        url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
        conn = psycopg2.connect(url, cursor_factory=DictCursor)
        return conn
    else:
        # Se l'app gira in locale sul tuo PC con SQLite
        conn = sqlite3.connect(os.path.join(BASE_DIR, 'falegnameria.db'))
        conn.row_factory = sqlite3.Row
        return conn

def execute_query(conn, query, params=()):
    """Helper per eseguire query in modo trasparente sia su SQLite che su PostgreSQL."""
    is_postgres = DATABASE_URL and HAS_PSYCOPG2
    if is_postgres:
        query = query.replace('?', '%s')
    cursor = conn.cursor()
    cursor.execute(query, params)
    return cursor

def init_db():
    conn = get_db_connection()
    is_postgres = DATABASE_URL and HAS_PSYCOPG2
    pk_type = "SERIAL PRIMARY KEY" if is_postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
    
    # 1. Impostazioni
    execute_query(conn, f'''
        CREATE TABLE IF NOT EXISTS impostazioni (
            id INTEGER PRIMARY KEY DEFAULT 1,
            nome_azienda TEXT NOT NULL,
            ragione_sociale TEXT,
            indirizzo TEXT,
            citta TEXT,
            cap TEXT,
            provincia TEXT,
            p_iva TEXT,
            codice_fiscale TEXT,
            pec TEXT,
            iban TEXT,
            telefono TEXT,
            email TEXT,
            sito_web TEXT,
            codice_univoco TEXT,
            logo_path TEXT DEFAULT 'static/logo.png',
            note_pie_pagina TEXT,
            pin_accesso TEXT DEFAULT '1234'
        )
    ''')

    # Inserimento record iniziale se vuoto
    cur = execute_query(conn, 'SELECT COUNT(*) FROM impostazioni')
    count = cur.fetchone()[0]
    if count == 0:
        execute_query(conn, '''
            INSERT INTO impostazioni (id, nome_azienda, ragione_sociale, indirizzo, citta, cap, provincia, p_iva, codice_fiscale, pec, iban, telefono, email, codice_univoco, logo_path, note_pie_pagina, pin_accesso)
            VALUES (1, 'Pietro Angius Falegnameria', 'Falegnameria Angius Pietro', 'Via Tuveri, 2', 'Bonorva', '07012', 'SS', '01301190904', 'NGSPTR66P291452C', 'angiuspietro@pec.it', 'IT09L0101584921000000001218', '3207181348', 'porte.ap@gmail.com', 'KRRH6B9', 'static/logo.png', 'acconto 50% all''ordine saldo ad avviso merce pronta', '1234')
        ''')

    # 2. Righe Documento
    execute_query(conn, f'''
        CREATE TABLE IF NOT EXISTS righe_documento (
            id {pk_type},
            documento_id INTEGER,
            codice_prod TEXT DEFAULT '',
            descrizione TEXT NOT NULL,
            quantita REAL DEFAULT 1,
            unita_misura TEXT DEFAULT 'pz',
            prezzo_unitario REAL DEFAULT 0,
            sconto REAL DEFAULT 0,
            aliquota_iva REAL DEFAULT 22,
            totale_riga REAL DEFAULT 0
        )
    ''')

    # 3. Anagrafica
    execute_query(conn, f'''
        CREATE TABLE IF NOT EXISTS anagrafica (
            id {pk_type},
            tipo TEXT NOT NULL,
            ragione_sociale TEXT NOT NULL,
            p_iva TEXT, codice_fiscale TEXT, codice_destinatario TEXT DEFAULT '0000000',
            pec TEXT, indirizzo TEXT, citta TEXT, cap TEXT, provincia TEXT, telefono TEXT, email TEXT
        )
    ''')

    # 4. Magazzino
    execute_query(conn, f'''
        CREATE TABLE IF NOT EXISTS magazzino (
            id {pk_type}, codice TEXT UNIQUE NOT NULL, descrizione TEXT NOT NULL,
            categoria TEXT NOT NULL, unita_misura TEXT NOT NULL, giacenza REAL DEFAULT 0,
            scorta_minima REAL DEFAULT 0, prezzo_acquisto REAL DEFAULT 0, prezzo_vendita REAL DEFAULT 0
        )
    ''')

    # 5. Prodotti
    execute_query(conn, f'''
        CREATE TABLE IF NOT EXISTS prodotti (
            id {pk_type}, codice TEXT UNIQUE, nome TEXT NOT NULL,
            categoria TEXT, prezzo_unitario REAL DEFAULT 0, aliquota_iva REAL DEFAULT 22, unita_misura TEXT DEFAULT 'pz'
        )
    ''')

    # 6. Documenti
    execute_query(conn, f'''
        CREATE TABLE IF NOT EXISTS documenti (
            id {pk_type}, tipo_doc TEXT NOT NULL, numero_doc INTEGER NOT NULL,
            anno INTEGER NOT NULL, data_doc TEXT NOT NULL, anagrafica_id INTEGER, stato_pagamento TEXT DEFAULT 'In Sospeso',
            note TEXT, totale_imponibile REAL DEFAULT 0, totale_iva REAL DEFAULT 0, totale_documento REAL DEFAULT 0
        )
    ''')

    # 7. Scadenziario
    execute_query(conn, f'''
        CREATE TABLE IF NOT EXISTS scadenziario (
            id {pk_type}, documento_id INTEGER, data_scadenza TEXT NOT NULL, importo REAL NOT NULL, stato TEXT DEFAULT 'In Sospeso'
        )
    ''')

    # 8. Agenda
    execute_query(conn, f'''
        CREATE TABLE IF NOT EXISTS agenda (
            id {pk_type}, titolo TEXT NOT NULL, data_evento TEXT NOT NULL, ora_evento TEXT, tipo_evento TEXT DEFAULT 'Promemoria', priorita TEXT DEFAULT 'Media', stato TEXT DEFAULT 'Da Fare', note TEXT, anagrafica_id INTEGER
        )
    ''')

    conn.commit()
    conn.close()

init_db()

# --- CONTROLLO DI PROTEZIONE CON PIN ---
@app.before_request
def check_pin_protection():
    if request.endpoint in ['login', 'static']:
        return
    if not session.get('authenticated'):
        return redirect(url_for('login'))

# --- AUTENTICAZIONE E LOGOUT ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    conn = get_db_connection()
    cur = execute_query(conn, 'SELECT * FROM impostazioni WHERE id = 1')
    azienda = cur.fetchone()
    conn.close()

    if request.method == 'POST':
        pin_inserito = request.form.get('pin', '')
        pin_corretto = azienda['pin_accesso'] if azienda and 'pin_accesso' in azienda.keys() and azienda['pin_accesso'] else '1234'
        
        if pin_inserito == pin_corretto:
            session['authenticated'] = True
            return redirect(url_for('index'))
        else:
            error = "PIN non corretto. Riprova."

    return render_template('login.html', azienda=azienda, error=error)

@app.route('/logout')
def logout():
    session.pop('authenticated', None)
    return redirect(url_for('login'))

# --- HELPER REPORTLAB ---
def safe_text(txt):
    if not txt:
        return ""
    s = str(txt)
    s = re.sub(r'<[^>]*>', '', s)
    s = s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    s = s.replace('\n', '<br/>')
    return s

def genera_pdf_documento(doc, cliente, righe, info_azienda):
    buffer = io.BytesIO()
    doc_pdf = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=1.0*cm, leftMargin=1.0*cm,
        topMargin=1.0*cm, bottomMargin=1.0*cm
    )
    
    styles = getSampleStyleSheet()
    BLUE_COLOR = colors.HexColor('#1A365D')
    
    title_style = ParagraphStyle('TitleStyle', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=18, leading=20, textColor=colors.black)
    norm_small = ParagraphStyle('NormSmall', parent=styles['Normal'], fontName='Helvetica', fontSize=8, leading=10.5, textColor=colors.black)
    bold_small = ParagraphStyle('BoldSmall', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=8.5, leading=11, textColor=colors.black)
    box_title_style = ParagraphStyle('BoxTitle', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=8.5, leading=10, textColor=colors.black)
    box_content_style = ParagraphStyle('BoxContent', parent=styles['Normal'], fontName='Helvetica', fontSize=9, leading=12, textColor=colors.black)
    box_content_bold = ParagraphStyle('BoxContentBold', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=9, leading=12, textColor=colors.black)
    table_header = ParagraphStyle('THeader', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=8.5, leading=10, textColor=colors.black)
    right_bold = ParagraphStyle('RightBold', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=8.5, alignment=2, leading=11)

    story = []

    def get_val(item, key, default=""):
        if item and key in item.keys() and item[key] is not None:
            return safe_text(item[key])
        return safe_text(default)

    logo_filename = info_azienda['logo_path'] if (info_azienda and info_azienda['logo_path']) else 'static/logo.png'
    
    candidate_paths = [
        os.path.join(BASE_DIR, logo_filename),
        os.path.join(BASE_DIR, 'static', 'logo.png'),
        os.path.join(BASE_DIR, 'static', 'logo.jpg'),
        os.path.join(BASE_DIR, 'static', 'logo.jpeg'),
        os.path.join(BASE_DIR, 'logo.png'),
        os.path.join(BASE_DIR, 'logo.jpg')
    ]

    found_logo_path = None
    for p in candidate_paths:
        if p and os.path.exists(p) and os.path.isfile(p):
            found_logo_path = p
            break

    logo_element = ""
    if found_logo_path:
        try:
            with PILImage.open(found_logo_path) as img_check:
                orig_w, orig_h = img_check.size
                
            max_w = 4.8 * cm
            max_h = 3.0 * cm
            aspect = orig_w / float(orig_h)

            if aspect > (max_w / max_h):
                final_w = max_w
                final_h = max_w / aspect
            else:
                final_h = max_h
                final_w = max_h * aspect

            img_obj = RLImage(found_logo_path, width=final_w, height=final_h)
            img_obj.hAlign = 'LEFT'
            logo_element = img_obj
        except Exception:
            logo_element = ""

    az_nome = get_val(info_azienda, 'nome_azienda', 'Pietro Angius Falegnameria')
    p_title = Paragraph(az_nome, title_style)

    t_header_top = Table([
        [logo_element, p_title]
    ], colWidths=[5.2*cm, 13.4*cm])
    t_header_top.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING', (1,0), (1,0), 10),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2),
    ]))
    story.append(t_header_top)

    az_ind = f"{get_val(info_azienda, 'indirizzo', 'Via Tuveri,2')} - {get_val(info_azienda, 'cap', '07012')} {get_val(info_azienda, 'citta', 'Bonorva')} ({get_val(info_azienda, 'provincia', 'SS')})"
    az_tel = f"Tel. CELL. {get_val(info_azienda, 'telefono', '3207181348')} / codice univoco {get_val(info_azienda, 'codice_univoco', 'KRRH6B9')}"
    az_mail = f"e-mail: {get_val(info_azienda, 'email', 'porte.ap@gmail.com')} Pec: {get_val(info_azienda, 'pec', 'angiuspietro@pec.it')}"
    az_cf_piva = f"C.F. {get_val(info_azienda, 'codice_fiscale', 'NGSPTR66P29I452C')} P.Iva {get_val(info_azienda, 'p_iva', '01301190904')}"
    az_iban = f"banco di sardegna IBAN {get_val(info_azienda, 'iban', 'IT09L0101584921000000001218')}"

    text_az_block = f"{az_ind}<br/>{az_tel}<br/>{az_mail}<br/>{az_cf_piva}<br/>{az_iban}"
    
    t_az_sub = Table([
        ["", Paragraph(text_az_block, norm_small)]
    ], colWidths=[5.2*cm, 13.4*cm])
    t_az_sub.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('LINEABOVE', (1,0), (1,0), 1.2, BLUE_COLOR),
        ('TOPPADDING', (1,0), (1,0), 4),
        ('LEFTPADDING', (1,0), (1,0), 10),
    ]))
    story.append(t_az_sub)
    story.append(Spacer(1, 10))

    num_doc = get_val(doc, 'numero_doc', '166')
    anno_doc = get_val(doc, 'anno', '2026')
    n_doc = f"{num_doc}/{anno_doc}" if 'C' in str(num_doc) else f"{num_doc}C/{anno_doc}"
    
    raw_date = get_val(doc, 'data_doc', '')
    try:
        dt_obj = datetime.strptime(raw_date, '%Y-%m-%d')
        data_doc_formatted = dt_obj.strftime('%d/%m/%Y')
    except Exception:
        data_doc_formatted = raw_date if raw_date else '17/10/2026'

    p_num_box = Paragraph(n_doc, ParagraphStyle('CBox', fontName='Helvetica-Bold', fontSize=9, alignment=1))
    p_date_box = Paragraph(data_doc_formatted, ParagraphStyle('CBox', fontName='Helvetica-Bold', fontSize=9, alignment=1))

    t_prev_boxes = Table([
        ["", Paragraph("Preventivo nr.", box_title_style), p_num_box, Paragraph("del", box_title_style), p_date_box]
    ], colWidths=[9.5*cm, 2.3*cm, 2.8*cm, 1.2*cm, 2.8*cm])
    t_prev_boxes.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BOX', (2,0), (2,0), 1.0, BLUE_COLOR),
        ('BOX', (4,0), (4,0), 1.0, BLUE_COLOR),
        ('TOPPADDING', (2,0), (2,0), 3),
        ('BOTTOMPADDING', (2,0), (2,0), 3),
        ('TOPPADDING', (4,0), (4,0), 3),
        ('BOTTOMPADDING', (4,0), (4,0), 3),
        ('LEFTPADDING', (0,0), (-1,-1), 0),
        ('RIGHTPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(t_prev_boxes)
    story.append(Spacer(1, 10))

    cl_rag = get_val(cliente, 'ragione_sociale', 'Mariagrazia')
    cl_ind_street = get_val(cliente, 'indirizzo', '')
    cl_ind_city = f"{get_val(cliente, 'cap', '07047')} {get_val(cliente, 'citta', 'THIESI')} ({get_val(cliente, 'provincia', 'SS')})".strip()

    cell_destinatario = [
        Paragraph("Destinatario", box_title_style), 
        Spacer(1, 4), 
        Paragraph(cl_rag, box_content_bold),
        Paragraph(f"{cl_ind_street}<br/>{cl_ind_city}".strip("<br/>"), box_content_style)
    ]
    
    cell_destinazione = [
        Paragraph("Destinazione", box_title_style), 
        Spacer(1, 4), 
        Paragraph(cl_rag, box_content_bold),
        Paragraph(f"{cl_ind_street}<br/>{cl_ind_city}".strip("<br/>"), box_content_style)
    ]

    t_boxes = Table([
        [cell_destinatario, "", cell_destinazione]
    ], colWidths=[9.0*cm, 0.6*cm, 9.0*cm])
    t_boxes.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('BOX', (0,0), (0,0), 1.0, BLUE_COLOR),
        ('BOX', (2,0), (2,0), 1.0, BLUE_COLOR),
        ('LEFTPADDING', (0,0), (0,0), 8),
        ('RIGHTPADDING', (0,0), (0,0), 8),
        ('TOPPADDING', (0,0), (0,0), 6),
        ('BOTTOMPADDING', (0,0), (0,0), 20),
        ('LEFTPADDING', (2,0), (2,0), 8),
        ('RIGHTPADDING', (2,0), (2,0), 8),
        ('TOPPADDING', (2,0), (2,0), 6),
        ('BOTTOMPADDING', (2,0), (2,0), 20),
    ]))
    story.append(t_boxes)
    story.append(Spacer(1, 15))

    data_table = [[
        Paragraph("Codice", table_header),
        Paragraph("Descrizione", table_header),
        Paragraph("Quantità", ParagraphStyle('THC', parent=table_header, alignment=1)),
        Paragraph("Prezzo", ParagraphStyle('THR', parent=table_header, alignment=2)),
        Paragraph("Sconto", ParagraphStyle('THR', parent=table_header, alignment=2)),
        Paragraph("Importo &nbsp;&nbsp;&nbsp;&nbsp; Iva", ParagraphStyle('THR', parent=table_header, alignment=2))
    ]]

    for r in righe:
        cod = get_val(r, 'codice_prod', '0899')
        if not cod:
            cod = '0899'
        um = get_val(r, 'unita_misura', 'pz')
        qta_val = float(r['quantita']) if r['quantita'] else 1.0
        prz_val = float(r['prezzo_unitario']) if r['prezzo_unitario'] else 600.0
        sconto_val = float(r['sconto']) if 'sconto' in r.keys() and r['sconto'] else 0.0
        iva_val = float(r['aliquota_iva']) if r['aliquota_iva'] else 22.0

        qta_str = f"{qta_val:.0f} &nbsp;&nbsp;&nbsp;&nbsp; {um}" if qta_val.is_integer() else f"{qta_val:.2f} {um}"
        tot_riga = qta_val * prz_val * (1 - (sconto_val / 100.0))
        sconto_str = f"{sconto_val:.0f}%" if sconto_val > 0 else ""

        desc_formatted = get_val(r, 'descrizione', '')
        importo_iva_str = f"€ {tot_riga:.2f} &nbsp;&nbsp;&nbsp;&nbsp; {int(iva_val)}"

        data_table.append([
            Paragraph(cod, norm_small),
            Paragraph(desc_formatted, norm_small),
            Paragraph(qta_str, ParagraphStyle('TC', parent=norm_small, alignment=1)),
            Paragraph(f"€ {prz_val:.2f}", ParagraphStyle('TR', parent=norm_small, alignment=2)),
            Paragraph(sconto_str, ParagraphStyle('TR', parent=norm_small, alignment=2)),
            Paragraph(importo_iva_str, ParagraphStyle('TR', parent=norm_small, alignment=2))
        ])

    t_prodotti = Table(data_table, colWidths=[1.5*cm, 7.5*cm, 2.2*cm, 2.0*cm, 1.8*cm, 3.6*cm])
    t_prodotti.setStyle(TableStyle([
        ('LINEBELOW', (0,0), (-1,0), 1.0, BLUE_COLOR),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('LEFTPADDING', (0,0), (-1,-1), 1),
        ('RIGHTPADDING', (0,0), (-1,-1), 1),
    ]))
    story.append(t_prodotti)
    story.append(Spacer(1, 20))

    pag_note = get_val(info_azienda, 'note_pie_pagina', "acconto 50% all'ordine saldo ad avviso merce pronta")
    raw_iban = get_val(info_azienda, 'iban', 'IT09L0101584921000000001218')
    iban_formatted = f"{raw_iban[:4]} {raw_iban[4:8]} {raw_iban[8:12]} {raw_iban[12:16]} {raw_iban[16:20]} {raw_iban[20:]}".strip()

    text_bottom_info = f"<b>Pagamento:</b> {pag_note}<br/>" \
                       f"<b>banco di sardegna- Ditta Pietro Angius</b><br/>" \
                       f"<b>IBAN</b> {iban_formatted}"

    tot_imp = float(doc['totale_imponibile']) if doc['totale_imponibile'] else 600.0
    tot_iva = float(doc['totale_iva']) if doc['totale_iva'] else 132.0
    tot_doc = float(doc['totale_documento']) if doc['totale_documento'] else 732.0

    t_totali = Table([
        [Paragraph("Acconto:", norm_small), Paragraph("", right_bold)],
        [Paragraph("Tot. imponibile", norm_small), Paragraph(f"€ {tot_imp:.2f}", right_bold)],
        [Paragraph("Tot. Iva", norm_small), Paragraph(f"€ {tot_iva:.2f}", right_bold)],
        [Paragraph("Tot. documento", bold_small), Paragraph(f"€ {tot_doc:.2f}", right_bold)]
    ], colWidths=[4.0*cm, 3.5*cm])
    
    t_totali.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
        ('TOPPADDING', (0,0), (-1,-1), 3),
    ]))

    t_middle_block = Table([
        [Paragraph(text_bottom_info, norm_small), t_totali]
    ], colWidths=[11.1*cm, 7.5*cm])
    t_middle_block.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('ALIGN', (1,0), (1,0), 'RIGHT'),
    ]))
    story.append(t_middle_block)
    story.append(Spacer(1, 15))

    story.append(Paragraph("Pag. 1", norm_small))
    story.append(Spacer(1, 6))

    legal_text = "Ai sensi del D.Lgs. 196/2003 Vi informiamo che i Vs. dati saranno utilizzati esclusivamente per i fini connessi ai rapporti commerciali tra di noi in essere.<br/>" \
                 "Contributo CONAI assolto ove dovuto - Vi preghiamo di controllare i Vs. dati anagrafici, la P. IVA e Il Cod. Fiscale. Non ci riteniamo responsabili di eventuali errori."
    
    t_legal = Table([[Paragraph(legal_text, ParagraphStyle('Legal', parent=norm_small, fontSize=7, leading=9, textColor=colors.HexColor('#4A5568')))]], colWidths=[18.6*cm])
    story.append(t_legal)

    doc_pdf.build(story)
    buffer.seek(0)
    return buffer

# --- ROTTA PRINCIPALE DASHBOARD CON FILTRI AVANZATI ---
@app.route('/')
def index():
    vista = request.args.get('vista', 'generale')
    tipo_filtro = request.args.get('tipo_filtro', 'anno')
    anno_selezionato = request.args.get('anno', datetime.now().year, type=int)
    data_inizio_str = request.args.get('data_inizio', '')
    data_fine_str = request.args.get('data_fine', '')

    dt_inizio = None
    dt_fine = None

    if tipo_filtro == 'periodo' and data_inizio_str and data_fine_str:
        try:
            dt_inizio = datetime.strptime(data_inizio_str, '%Y-%m-%d')
            dt_fine = datetime.strptime(data_fine_str, '%Y-%m-%d')
        except ValueError:
            tipo_filtro = 'anno'

    conn = get_db_connection()

    fatturato_attivo_mesi = [0.0] * 12
    fatturato_passivo_mesi = [0.0] * 12
    saldo_iva_mesi = [0.0] * 12
    incassato_mesi = [0.0] * 12
    scaduto_mesi = [0.0] * 12

    def data_in_filtro(data_doc_str):
        if not data_doc_str:
            return False, None, None
        data_clean = str(data_doc_str).strip()
        dt_doc = None
        for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%Y/%m/%d'):
            try:
                dt_doc = datetime.strptime(data_clean, fmt)
                break
            except ValueError:
                pass
        if not dt_doc:
            return False, None, None

        if tipo_filtro == 'periodo' and dt_inizio and dt_fine:
            valido = (dt_inizio <= dt_doc <= dt_fine)
        else:
            valido = (dt_doc.year == anno_selezionato)

        return valido, dt_doc.month, dt_doc.year

    documenti = execute_query(conn, '''
        SELECT d.data_doc, d.tipo_doc, d.totale_documento, d.totale_iva, a.tipo as tipo_anagrafica
        FROM documenti d
        LEFT JOIN anagrafica a ON d.anagrafica_id = a.id
    ''').fetchall()

    for doc in documenti:
        valido, m, y = data_in_filtro(doc['data_doc'])
        if valido:
            idx = m - 1
            tot_doc = float(doc['totale_documento'] or 0.0)
            tot_iva = float(doc['totale_iva'] or 0.0)

            if doc['tipo_doc'] == 'Fattura':
                fatturato_attivo_mesi[idx] += tot_doc
                saldo_iva_mesi[idx] += tot_iva
            elif doc['tipo_anagrafica'] == 'Fornitore':
                fatturato_passivo_mesi[idx] += tot_doc
                saldo_iva_mesi[idx] -= tot_iva

    scadenze = execute_query(conn, '''
        SELECT s.data_scadenza, s.importo, s.stato
        FROM scadenziario s
    ''').fetchall()

    for sc in scadenze:
        valido, m, y = data_in_filtro(sc['data_scadenza'])
        if valido:
            idx = m - 1
            imp = float(sc['importo'] or 0.0)
            if sc['stato'] == 'Saldato':
                incassato_mesi[idx] += imp
            else:
                scaduto_mesi[idx] += imp

    tot_clienti = execute_query(conn, "SELECT COUNT(*) FROM anagrafica WHERE tipo = 'Cliente'").fetchone()[0]
    tot_fornitori = execute_query(conn, "SELECT COUNT(*) FROM anagrafica WHERE tipo = 'Fornitore'").fetchone()[0]
    fatturato_totale = sum(fatturato_attivo_mesi)
    incassato_totale = sum(incassato_mesi)
    sospeso_totale = execute_query(conn, "SELECT SUM(importo) FROM scadenziario WHERE stato != 'Saldato'").fetchone()[0] or 0.0
    scorte_basse = execute_query(conn, "SELECT COUNT(*) FROM magazzino WHERE giacenza <= scorta_minima").fetchone()[0]

    entrate = execute_query(conn, '''
        SELECT d.numero_doc, d.anno, d.data_doc, d.totale_documento, a.ragione_sociale, s.stato as stato_scad
        FROM documenti d 
        JOIN anagrafica a ON d.anagrafica_id = a.id 
        JOIN scadenziario s ON s.documento_id = d.id
        WHERE d.tipo_doc = 'Fattura' AND s.stato != 'Saldato'
        ORDER BY d.id DESC
    ''').fetchall()

    uscite = execute_query(conn, '''
        SELECT d.numero_doc, d.anno, d.data_doc, d.totale_documento, a.ragione_sociale 
        FROM documenti d JOIN anagrafica a ON d.anagrafica_id = a.id 
        WHERE a.tipo = 'Fornitore' ORDER BY d.id DESC
    ''').fetchall()

    conn.close()

    return render_template('index.html', 
                           vista=vista,
                           tipo_filtro=tipo_filtro,
                           anno_selezionato=anno_selezionato,
                           data_inizio=data_inizio_str,
                           data_fine=data_fine_str,
                           clienti=tot_clienti, 
                           fornitori=tot_fornitori, 
                           fatturato=fatturato_totale, 
                           incassato_totale=incassato_totale,
                           sospeso=sospeso_totale, 
                           scorte=scorte_basse,
                           entrate=entrate, 
                           uscite=uscite,
                           fatturato_attivo_mesi=fatturato_attivo_mesi,
                           fatturato_passivo_mesi=fatturato_passivo_mesi,
                           saldo_iva_mesi=saldo_iva_mesi,
                           incassato_mesi=incassato_mesi,
                           scaduto_mesi=scaduto_mesi)

# --- ROTTE IMPOSTAZIONI ---
@app.route('/impostazioni', methods=['GET', 'POST'])
def impostazioni():
    conn = get_db_connection()
    if request.method == 'POST':
        nome_azienda = request.form.get('nome_azienda', '')
        ragione_sociale = request.form.get('ragione_sociale', '')
        indirizzo = request.form.get('indirizzo', '')
        citta = request.form.get('citta', '')
        cap = request.form.get('cap', '')
        provincia = request.form.get('provincia', '')
        p_iva = request.form.get('p_iva', '')
        codice_fiscale = request.form.get('codice_fiscale', '')
        pec = request.form.get('pec', '')
        iban = request.form.get('iban', '')
        telefono = request.form.get('telefono', '')
        email = request.form.get('email', '')
        codice_univoco = request.form.get('codice_univoco', '')
        logo_path = request.form.get('logo_path', 'static/logo.png')
        note_pie_pagina = request.form.get('note_pie_pagina', '')
        pin_accesso = request.form.get('pin_accesso', '1234')
        
        execute_query(conn, '''
            UPDATE impostazioni SET 
                nome_azienda = ?, ragione_sociale = ?, indirizzo = ?, citta = ?, cap = ?, 
                provincia = ?, p_iva = ?, codice_fiscale = ?, pec = ?, iban = ?, 
                telefono = ?, email = ?, codice_univoco = ?, logo_path = ?, note_pie_pagina = ?, pin_accesso = ?
            WHERE id = 1
        ''', (nome_azienda, ragione_sociale, indirizzo, citta, cap, provincia, p_iva, codice_fiscale, pec, iban, telefono, email, codice_univoco, logo_path, note_pie_pagina, pin_accesso))
        conn.commit()
        conn.close()
        return redirect(url_for('impostazioni'))

    azienda = execute_query(conn, 'SELECT * FROM impostazioni WHERE id = 1').fetchone()
    conn.close()
    return render_template('impostazioni.html', azienda=azienda)

# --- ROTTE ANAGRAFICA ---
@app.route('/anagrafica', methods=['GET', 'POST'])
def anagrafica():
    conn = get_db_connection()
    if request.method == 'POST':
        tipo = request.form['tipo']
        ragione_sociale = request.form['ragione_sociale']
        p_iva = request.form['p_iva']
        codice_fiscale = request.form['codice_fiscale']
        codice_destinatario = request.form.get('codice_destinatario', '0000000')
        pec = request.form['pec']
        indirizzo = request.form['indirizzo']
        citta = request.form['citta']
        cap = request.form['cap']
        provincia = request.form['provincia']
        telefono = request.form['telefono']
        email = request.form['email']
        
        execute_query(conn, '''
            INSERT INTO anagrafica (tipo, ragione_sociale, p_iva, codice_fiscale, codice_destinatario, pec, indirizzo, citta, cap, provincia, telefono, email)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (tipo, ragione_sociale, p_iva, codice_fiscale, codice_destinatario, pec, indirizzo, citta, cap, provincia, telefono, email))
        conn.commit()
        conn.close()
        return redirect(url_for('anagrafica'))
    
    elenco = execute_query(conn, 'SELECT * FROM anagrafica ORDER BY ragione_sociale ASC').fetchall()
    conn.close()
    return render_template('anagrafica.html', contatti=elenco)

@app.route('/elimina_anagrafica/<int:id>')
def elimina_anagrafica(id):
    conn = get_db_connection()
    execute_query(conn, 'DELETE FROM anagrafica WHERE id = ?', (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('anagrafica'))

# --- ROTTE PRODOTTI ---
@app.route('/prodotti', methods=['GET', 'POST'])
def prodotti():
    conn = get_db_connection()
    if request.method == 'POST':
        codice = request.form.get('codice', '')
        nome = request.form['nome']
        categoria = request.form.get('categoria', '')
        prezzo_unitario = float(request.form.get('prezzo_unitario', 0))
        aliquota_iva = float(request.form.get('aliquota_iva', 22))
        unita_misura = request.form.get('unita_misura', 'pz')
        
        execute_query(conn, '''
            INSERT INTO prodotti (codice, nome, categoria, prezzo_unitario, aliquota_iva, unita_misura)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (codice, nome, categoria, prezzo_unitario, aliquota_iva, unita_misura))
        conn.commit()
        conn.close()
        return redirect(url_for('prodotti'))
        
    elenco_prodotti = execute_query(conn, 'SELECT * FROM prodotti ORDER BY nome ASC').fetchall()
    conn.close()
    return render_template('prodotti.html', prodotti=elenco_prodotti)

@app.route('/modifica_prodotto/<int:id>', methods=['POST'])
def modifica_prodotto(id):
    conn = get_db_connection()
    codice = request.form.get('codice', '')
    nome = request.form.get('nome', '')
    prezzo_unitario = float(request.form.get('prezzo_unitario', 0.0))
    unita_misura = request.form.get('unita_misura', 'pz')
    aliquota_iva = float(request.form.get('aliquota_iva', 22.0))

    execute_query(conn, '''
        UPDATE prodotti SET codice = ?, nome = ?, prezzo_unitario = ?, unita_misura = ?, aliquota_iva = ?
        WHERE id = ?
    ''', (codice, nome, prezzo_unitario, unita_misura, aliquota_iva, id))
    conn.commit()
    conn.close()
    return redirect(url_for('prodotti'))

@app.route('/elimina_prodotto/<int:id>')
def elimina_prodotto(id):
    conn = get_db_connection()
    execute_query(conn, 'DELETE FROM prodotti WHERE id = ?', (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('prodotti'))

# --- ROTTE MAGAZZINO ---
@app.route('/magazzino', methods=['GET', 'POST'])
def magazzino():
    conn = get_db_connection()
    if request.method == 'POST':
        codice = request.form['codice']
        descrizione = request.form['descrizione']
        categoria = request.form['categoria']
        unita_misura = request.form['unita_misura']
        giacenza = request.form['giacenza']
        scorta_minima = request.form['scorta_minima']
        prezzo_acquisto = request.form['prezzo_acquisto']
        prezzo_vendita = request.form['prezzo_vendita']
        
        execute_query(conn, '''
            INSERT INTO magazzino (codice, descrizione, categoria, unita_misura, giacenza, scorta_minima, prezzo_acquisto, prezzo_vendita)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (codice, descrizione, categoria, unita_misura, giacenza, scorta_minima, prezzo_acquisto, prezzo_vendita))
        conn.commit()
        conn.close()
        return redirect(url_for('magazzino'))
    
    articoli = execute_query(conn, 'SELECT * FROM magazzino ORDER BY descrizione ASC').fetchall()
    conn.close()
    return render_template('magazzino.html', articoli=articoli)

# --- ROTTE DOCUMENTI E PDF ---
@app.route('/documenti', methods=['GET', 'POST'])
def documenti():
    conn = get_db_connection()
    if request.method == 'POST':
        tipo_doc = request.form['tipo_doc']
        numero_doc = request.form['numero_doc']
        data_doc = request.form['data_doc']
        anagrafica_id = request.form['anagrafica_id']
        
        m, anno = None, None
        if data_doc:
            try:
                dt_d = datetime.strptime(data_doc, '%Y-%m-%d')
                m, anno = dt_d.month, dt_d.year
            except ValueError:
                pass
        if not anno:
            anno = datetime.now().year

        codici_list = request.form.getlist('codice[]')
        descrizioni = request.form.getlist('descrizione[]')
        quantita_list = request.form.getlist('quantita[]')
        unita_list = request.form.getlist('unita_misura[]')
        prezzi_list = request.form.getlist('prezzo_unitario[]')
        sconti_list = request.form.getlist('sconto[]')
        iva_list = request.form.getlist('aliquota_iva[]')

        tot_imponibile = 0.0
        tot_iva = 0.0

        is_postgres = DATABASE_URL and HAS_PSYCOPG2
        if is_postgres:
            cursor = execute_query(conn, '''
                INSERT INTO documenti (tipo_doc, numero_doc, anno, data_doc, anagrafica_id, totale_imponibile, totale_iva, totale_documento)
                VALUES (?, ?, ?, ?, ?, 0, 0, 0) RETURNING id
            ''', (tipo_doc, numero_doc, anno, data_doc, anagrafica_id))
            doc_id = cursor.fetchone()[0]
        else:
            cursor = execute_query(conn, '''
                INSERT INTO documenti (tipo_doc, numero_doc, anno, data_doc, anagrafica_id, totale_imponibile, totale_iva, totale_documento)
                VALUES (?, ?, ?, ?, ?, 0, 0, 0)
            ''', (tipo_doc, numero_doc, anno, data_doc, anagrafica_id))
            doc_id = cursor.lastrowid

        for cod, desc, qta, um, prz, sc, iva in zip(codici_list, descrizioni, quantita_list, unita_list, prezzi_list, sconti_list, iva_list):
            qta_val = float(qta) if qta else 0.0
            prz_val = float(prz) if prz else 0.0
            sc_val = float(sc) if sc else 0.0
            iva_val = float(iva) if iva else 22.0
            
            imp_lordo = qta_val * prz_val
            imp_riga = imp_lordo * (1 - (sc_val / 100.0))
            iva_riga = imp_riga * (iva_val / 100.0)
            
            tot_imponibile += imp_riga
            tot_iva += iva_riga
            
            execute_query(conn, '''
                INSERT INTO righe_documento (documento_id, codice_prod, descrizione, quantita, unita_misura, prezzo_unitario, sconto, aliquota_iva, totale_riga)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (doc_id, cod, desc, qta_val, um, prz_val, sc_val, iva_val, imp_riga + iva_riga))

        tot_doc = tot_imponibile + tot_iva
        execute_query(conn, '''
            UPDATE documenti SET totale_imponibile = ?, totale_iva = ?, totale_documento = ? WHERE id = ?
        ''', (tot_imponibile, tot_iva, tot_doc, doc_id))

        if tipo_doc == 'Fattura':
            scadenza = request.form.get('data_scadenza', data_doc)
            execute_query(conn, '''
                INSERT INTO scadenziario (documento_id, data_scadenza, importo, stato)
                VALUES (?, ?, ?, 'In Sospeso')
            ''', (doc_id, scadenza, tot_doc))

        conn.commit()
        conn.close()
        return redirect(url_for('documenti'))

    elenco_doc = execute_query(conn, '''
        SELECT d.*, a.ragione_sociale 
        FROM documenti d 
        LEFT JOIN anagrafica a ON d.anagrafica_id = a.id 
        ORDER BY d.id DESC
    ''').fetchall()
    
    anagrafiche = execute_query(conn, 'SELECT id, ragione_sociale FROM anagrafica ORDER BY ragione_sociale ASC').fetchall()
    prodotti_listino = execute_query(conn, 'SELECT * FROM prodotti ORDER BY nome ASC').fetchall()
    conn.close()
    return render_template('documenti.html', documenti=elenco_doc, anagrafiche=anagrafiche, prodotti=prodotti_listino)

@app.route('/elimina_documento/<int:doc_id>')
def elimina_documento(doc_id):
    conn = get_db_connection()
    execute_query(conn, 'DELETE FROM righe_documento WHERE documento_id = ?', (doc_id,))
    execute_query(conn, 'DELETE FROM scadenziario WHERE documento_id = ?', (doc_id,))
    execute_query(conn, 'DELETE FROM documenti WHERE id = ?', (doc_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('documenti'))

@app.route('/modifica_documento/<int:doc_id>', methods=['GET', 'POST'])
def modifica_documento(doc_id):
    conn = get_db_connection()
    
    if request.method == 'POST':
        tipo_doc = request.form['tipo_doc']
        numero_doc = request.form['numero_doc']
        data_doc = request.form['data_doc']
        anagrafica_id = request.form['anagrafica_id']
        
        m, anno = None, None
        if data_doc:
            try:
                dt_d = datetime.strptime(data_doc, '%Y-%m-%d')
                m, anno = dt_d.month, dt_d.year
            except ValueError:
                pass
        if not anno:
            anno = datetime.now().year
        
        codici_list = request.form.getlist('codice[]')
        descrizioni = request.form.getlist('descrizione[]')
        quantita_list = request.form.getlist('quantita[]')
        unita_list = request.form.getlist('unita_misura[]')
        prezzi_list = request.form.getlist('prezzo_unitario[]')
        sconti_list = request.form.getlist('sconto[]')
        iva_list = request.form.getlist('aliquota_iva[]')

        execute_query(conn, 'DELETE FROM righe_documento WHERE documento_id = ?', (doc_id,))
        
        tot_imponibile = 0.0
        tot_iva = 0.0

        for cod, desc, qta, um, prz, sc, iva in zip(codici_list, descrizioni, quantita_list, unita_list, prezzi_list, sconti_list, iva_list):
            qta_val = float(qta) if qta else 0.0
            prz_val = float(prz) if prz else 0.0
            sc_val = float(sc) if sc else 0.0
            iva_val = float(iva) if iva else 22.0
            
            imp_lordo = qta_val * prz_val
            imp_riga = imp_lordo * (1 - (sc_val / 100.0))
            iva_riga = imp_riga * (iva_val / 100.0)
            
            tot_imponibile += imp_riga
            tot_iva += iva_riga
            
            execute_query(conn, '''
                INSERT INTO righe_documento (documento_id, codice_prod, descrizione, quantita, unita_misura, prezzo_unitario, sconto, aliquota_iva, totale_riga)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (doc_id, cod, desc, qta_val, um, prz_val, sc_val, iva_val, imp_riga + iva_riga))

        tot_doc = tot_imponibile + tot_iva
        execute_query(conn, '''
            UPDATE documenti 
            SET tipo_doc = ?, numero_doc = ?, anno = ?, data_doc = ?, anagrafica_id = ?, 
                totale_imponibile = ?, totale_iva = ?, totale_documento = ? 
            WHERE id = ?
        ''', (tipo_doc, numero_doc, anno, data_doc, anagrafica_id, tot_imponibile, tot_iva, tot_doc, doc_id))

        execute_query(conn, 'UPDATE scadenziario SET importo = ? WHERE documento_id = ?', (tot_doc, doc_id))

        conn.commit()
        conn.close()
        return redirect(url_for('documenti'))

    doc = execute_query(conn, 'SELECT * FROM documenti WHERE id = ?', (doc_id,)).fetchone()
    righe = execute_query(conn, 'SELECT * FROM righe_documento WHERE documento_id = ?', (doc_id,)).fetchall()
    anagrafiche = execute_query(conn, 'SELECT id, ragione_sociale FROM anagrafica ORDER BY ragione_sociale ASC').fetchall()
    prodotti_listino = execute_query(conn, 'SELECT * FROM prodotti ORDER BY nome ASC').fetchall()
    conn.close()
    
    return render_template('modifica_documento.html', doc=doc, righe=righe, anagrafiche=anagrafiche, prodotti=prodotti_listino)

@app.route('/pdf_documento/<int:doc_id>')
def pdf_documento(doc_id):
    conn = get_db_connection()
    doc = execute_query(conn, 'SELECT * FROM documenti WHERE id = ?', (doc_id,)).fetchone()
    cliente = execute_query(conn, 'SELECT * FROM anagrafica WHERE id = ?', (doc['anagrafica_id'],)).fetchone()
    righe = execute_query(conn, 'SELECT * FROM righe_documento WHERE documento_id = ?', (doc_id,)).fetchall()
    azienda = execute_query(conn, 'SELECT * FROM impostazioni WHERE id = 1').fetchone()
    conn.close()

    pdf_buffer = genera_pdf_documento(doc, cliente, righe, azienda)
    filename = f"{doc['tipo_doc']}_{doc['numero_doc']}_{doc['anno']}.pdf"

    return send_file(
        pdf_buffer,
        as_attachment=False,
        mimetype='application/pdf',
        download_name=filename
    )

@app.route('/firma_documento/<int:doc_id>', methods=['GET', 'POST'])
def firma_documento(doc_id):
    conn = get_db_connection()
    doc = execute_query(conn, 'SELECT * FROM documenti WHERE id = ?', (doc_id,)).fetchone()
    
    if request.method == 'POST':
        execute_query(conn, "UPDATE documenti SET note = 'Firmato Digitalmente con Aruba' WHERE id = ?", (doc_id,))
        conn.commit()
        conn.close()
        return redirect(url_for('documenti'))
            
    conn.close()
    return render_template('firma_aruba.html', doc=doc)

# --- ROTTE AGENDA CON ELIMINAZIONE ---
@app.route('/agenda', methods=['GET', 'POST'])
def agenda():
    conn = get_db_connection()
    if request.method == 'POST':
        titolo = request.form['titolo']
        data_evento = request.form['data_evento']
        ora_evento = request.form.get('ora_evento', '')
        tipo_evento = request.form['tipo_evento']
        priorita = request.form['priorita']
        note = request.form.get('note', '')
        anagrafica_id = request.form.get('anagrafica_id') or None
        
        execute_query(conn, '''
            INSERT INTO agenda (titolo, data_evento, ora_evento, tipo_evento, priorita, note, anagrafica_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (titolo, data_evento, ora_evento, tipo_evento, priorita, note, anagrafica_id))
        conn.commit()
        conn.close()
        return redirect(url_for('agenda'))
    
    eventi = execute_query(conn, '''
        SELECT ag.*, a.ragione_sociale 
        FROM agenda ag 
        LEFT JOIN anagrafica a ON ag.anagrafica_id = a.id 
        ORDER BY ag.data_evento ASC, ag.ora_evento ASC
    ''').fetchall()
    
    anagrafiche = execute_query(conn, 'SELECT id, ragione_sociale FROM anagrafica ORDER BY ragione_sociale ASC').fetchall()
    conn.close()
    return render_template('agenda.html', eventi=eventi, anagrafiche=anagrafiche)

@app.route('/completa_evento/<int:evento_id>')
def completa_evento(evento_id):
    conn = get_db_connection()
    execute_query(conn, "UPDATE agenda SET stato = 'Completato' WHERE id = ?", (evento_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('agenda'))

@app.route('/elimina_evento/<int:evento_id>')
def elimina_evento(evento_id):
    conn = get_db_connection()
    execute_query(conn, 'DELETE FROM agenda WHERE id = ?', (evento_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('agenda'))

# --- ROTTE SCADENZIARIO ---
@app.route('/scadenziario')
def scadenziario():
    conn = get_db_connection()
    scadenze = execute_query(conn, '''
        SELECT s.*, d.numero_doc, d.tipo_doc, d.anno, a.ragione_sociale 
        FROM scadenziario s
        JOIN documenti d ON s.documento_id = d.id
        JOIN anagrafica a ON d.anagrafica_id = a.id
        ORDER BY s.data_scadenza ASC
    ''').fetchall()
    conn.close()
    return render_template('scadenziario.html', scadenze=scadenze)

@app.route('/salda_pagamento/<int:scadenza_id>')
def salda_pagamento(scadenza_id):
    conn = get_db_connection()
    scadenza = execute_query(conn, "SELECT * FROM scadenziario WHERE id = ?", (scadenza_id,)).fetchone()
    
    if scadenza:
        execute_query(conn, "UPDATE scadenziario SET stato = 'Saldato' WHERE id = ?", (scadenza_id,))
        execute_query(conn, "UPDATE documenti SET stato_pagamento = 'Saldato' WHERE id = ?", (scadenza['documento_id'],))
        conn.commit()
        
    conn.close()
    return redirect(url_for('scadenziario'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)