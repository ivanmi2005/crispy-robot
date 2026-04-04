"""
Recategoriza todos los canales de raul_channels.json
segun las categorias estandar de la lista.
"""
import json, re
from pathlib import Path

FILE = Path("C:/Users/ivanm/Desktop/aaa/M3U/raul_channels.json")

# Hash de 40 chars = valido
HASH_RE = re.compile(r'^[0-9a-fA-F]{40}$')

# ----------- reglas en orden de prioridad -----------
# Cada regla: (patron_regex_en_nombre_UPPER, categoria)
RULES = [
    # --- COPA DEL REY ---
    (r'COPA DEL REY',                           'COPA DEL REY'),

    # --- 1RFEF ---
    (r'1RFEF|RFEF|PRIMERA\s+FED|FEF TV|PRIMERA\s+RFEF', '1RFEF'),

    # --- FORMULA 1 ---
    (r'DAZN\s*F1|F1\s*TV|FORMULA\s*1|SKY SPORT.*F1', 'FORMULA 1'),

    # --- MOTOR (MotoGP, Rally, NASCAR) ---
    (r'MOTOGP|MOTO.?GP|RALLY|NASCAR|SKY SPORT.*MOTO', 'MOTOR'),

    # --- HYPERMOTION ---
    (r'HYPERMOTION',                            'HYPERMOTION'),

    # --- LIGA DE CAMPEONES (Campeones = Champions) ---
    (r'CAMPEONES|CHAMPIONS|LIGA\s+DE\s+CAMPEONES', 'LIGA DE CAMPEONES'),

    # --- LIGA ENDESA / Baloncesto ---
    (r'ACB\s+EVENTO|BALONCESTO|LIGA\s+ENDESA|BASKET', 'LIGA ENDESA'),

    # --- NBA ---
    (r'\bNBA\b',                                'NBA'),

    # --- TENNIS ---
    (r'TENNIS\s+CHANNEL|TENIS\s+CHANNEL',       'TENNIS'),

    # --- EUROSPORT ---
    (r'EUROSPORT',                              'EUROSPORT'),

    # --- LA LIGA (Movistar Liga, DAZN Liga, Sky Liga) ---
    (r'M[\.\+]\s*LIGA|DAZN\s+LIGA|DAZN\s*LA.?LIGA|SKY\s*SPORT[S]?\s*LIGA|SKY\s*SPORT[S]?\s*LALIGA', 'LA LIGA'),

    # --- MOVISTAR DEPORTES (M. Deportes 1-8, Deportes 1-8 numerados) ---
    (r'DEPORTES\s*[2-9]\b|DEPORTES\s*\d{1,2}\s*(FHD|HD|720|1080|$)|M[\.\s]+DEPORTES\s*\d|DEPORTES FHD', 'MOVISTAR DEPORTES'),
    # Deportes sin numero pero sigue siendo M. Deportes si viene con resolucion
    (r'^DEPORTES\s*(720|1080|FHD|HD)',          'MOVISTAR DEPORTES'),

    # --- MOVISTAR (canales entretenimiento M+) ---
    (r'M\+\s*ACCION|M\+ACCION|M\.\s*ACCION|VAMOS\s*(FHD|[23]?\s*720|[23]?\s*1080|\s*$)|'
     r'M\+PLUS|M\+\s*PLUS|M\.PLUS|M\+GOLF|M\.\s*GOLF|'
     r'ESTRENOS|ELLAS FHD|CANAL COCINA|CAZA Y PESCA|DARK HD|DECASA|ONETORO|'
     r'M\+\s*COMEDIA|M\+\s*TERROR|M\+\s*DRAMA|M\+\s*HITS|M\+\s*INDIE|M\+\s*ROMANCE|'
     r'M\+\s*CINE|M\+\s*W.?STERN|M\+\s*DOCUMENTALES|M\+\s*ORIGINALES|M\+\s*SERIE|'
     r'SKYSHOWTIME|HBO\s*MAX\s*AVANCES|SYFY\s*HD|DISNEY\s*JUNIOR|DISNEY\s*CHANNEL|'
     r'MTV\s*SPAIN|BOING\s*SPAIN|NEOX\s*SPAIN|NOVA\s*SPAIN|CALLE\s*13|'
     r'M\+\s*ACCION\s*HD|M\+\s*TERROR|TVG\s*EUROPA|TOROLE|AMC\s*HD|'
     r'ANTENA\s*3\s*FHD|MIXED\s*TV', 'MOVISTAR'),

    # --- TDT ---
    (r'\bLA\s*1\b|\bLA\s*2\b|TVE\s*LA|CUATRO\s*(FHD|HD|720|1080|$)|'
     r'LA\s*SEXTA|LASEXTA|TELECINCO|ANTENA\s*3\s*(720|1080|SPAIN|$)|'
     r'24\s*H(ORAS)?\s*(TV|HD|720|1080|$)|TELEDEPORTE\s*(720|1080|FHD|SPAIN)|'
     r'\bBOING\b|\bNEOX\b|\bNOVA\b|\bLA\s*OTRA\b|'
     r'LA\s*1\s*(720|1080|4K|FHD|SPAIN)|LA\s*1\s*HD', 'TDT'),

    # --- DAZN principal (1-4, con o sin BAR, sin calificadores especificos) ---
    (r'^DAZN\s*[1-4]\s*(FHD|HD|720P|1080P|BAR|$)',  'DAZN'),
    (r'^DAZN\s*[1-4]\s+BAR',                    'DAZN'),
    (r'^DAZN\s+[1-4]$',                         'DAZN'),

    # --- DEPORTES (fox, direct sport, gol play, real madrid tv, sky sports generico, gol) ---
    (r'FOX\s*SPORT|DIRECT\s*SPORT|GOL\s*PLAY|REAL\s*MADRID\s*TV|'
     r'GOL\s*PLAY|TELEDEPORTE|S\s*SPORT\s*1|'
     r'SKY\s*SPORT[S]?\s*(ARENA|CRICKET|MAIN|PREMIER)|'
     r'UFC\s*FIGHT|DAZN\s*EVENTOS|DEPORTES\s*(720|1080|FHD)\s*$|'
     r'^DEPORTES\s*(FHD|HD|720|1080)\s*3|LIGA\s*TV\s*M', 'DEPORTES'),

    # --- FUTBOL INT (ESPN, ligas internacionales, canales extranjeros) ---
    (r'ESPN|LIGA\s*1\s*MAX|GOL\s*PERU|ELEVEN\s*SPORT|DIEMA\s*SPORT|'
     r'ZIGGO\s*SPORT|TNT\s*SPORT[S]?|POLSAT\s*SPORT|BEIN\s*SPORT[S]?.*FRANCE|'
     r'OKKO\s*SPORT|SKY\s*SPORT\s*[1-4]\s*AUSTRIA|'
     r'TV5MONDE|ESPORT\s*3|ESPORT3|DAZN\s*SERIE\s*A', 'FUTBOL INT'),

    # --- Canales con variante turca u otras (Bein Sports TR, S Sport) ---
    (r'BEIN\s*SPORT[S]?\s*TR|S\s*SPORT\s*[12]',    'FUTBOL INT'),

    # --- EVENTOS ---
    (r'EVENTO\b|DAZN\s*EVENTOS',                'EVENTOS'),

    # --- OTROS que necesitan regla especifica ---
    # M+ Golf sin el + (por encoding roto)
    (r'M[\.\s]*GOLF|M\+\s*GOLF',               'MOVISTAR'),
    # M+ Accion con encoding roto
    (r'M\+?\s*ACCI',                            'MOVISTAR'),
    # DAZN generico (sin numero — DAZN 5, DAZN 6, etc.)
    (r'^DAZN\s*\d?\s*$',                        'DAZN'),
    # DAZN con numero y solo sufijo numerico corto (ej "DAZN 1   1")
    (r'^DAZN\s+[1-4]\s+\d\s*$',                'DAZN'),
    # M. Deportes HD
    (r'M\.\s*DEPORTES|DEPORTES\s*HD',          'MOVISTAR DEPORTES'),
    # NBC Sports
    (r'NBC\s*SPORT',                            'FUTBOL INT'),
    # TV3 CAT
    (r'TV3\s*CAT|TV3\s*CATALAN',               'TDT'),
    # Servus TV (F1/Motor austriaco)
    (r'SERVUS\s*TV',                            'MOTOR'),
    # ESPN MX con typo ESSPN
    (r'ESSPN|ESPN\s*MX',                        'FUTBOL INT'),
    # Primera FHD → 1RFEF
    (r'^PRIMERA\s*(FHD|HD|720|1080)',           '1RFEF'),
    # NFL → DEPORTES
    (r'\bNFL\b',                                'DEPORTES'),
    # Baby TV, AXN Movies → MOVISTAR
    (r'BABY\s*TV|AXN\s*MOVIE|M\+\s*INDIE',     'MOVISTAR'),
]

HASH_BAD_NAME_RE = re.compile(
    r'^\d{1,2}:\d{2}]|^\d+/\d+/\d+|^admin|^https?://',
    re.IGNORECASE
)

def categorize(name: str) -> str:
    n = name.upper().strip()
    for pattern, cat in RULES:
        if re.search(pattern, n):
            return cat
    return 'OTROS'

def is_valid(ch: dict) -> bool:
    h = ch.get('hash', '').strip()
    name = ch.get('name', '').strip()
    if not HASH_RE.match(h):
        return False
    if not name or HASH_BAD_NAME_RE.match(name):
        return False
    return True

def main():
    channels = json.loads(FILE.read_text(encoding='utf-8'))
    print(f"Total antes: {len(channels)}")

    # Filtrar entradas invalidas
    valid = [c for c in channels if is_valid(c)]
    removed = len(channels) - len(valid)
    print(f"Eliminadas entradas invalidas: {removed}")

    # Recategorizar
    counts: dict[str, int] = {}
    for ch in valid:
        old = ch.get('group', 'OTROS')
        new = categorize(ch['name'])
        ch['group'] = new
        counts[new] = counts.get(new, 0) + 1

    # Dedup por hash (conservar primera aparicion)
    seen: set[str] = set()
    deduped = []
    dupes = 0
    for ch in valid:
        if ch['hash'] in seen:
            dupes += 1
        else:
            seen.add(ch['hash'])
            deduped.append(ch)
    print(f"Duplicados eliminados: {dupes}")
    print(f"Total final: {len(deduped)}")
    print()
    print("Canales por categoria:")
    for cat, n in sorted(counts.items()):
        print(f"  {cat:<25} {n}")

    FILE.write_text(json.dumps(deduped, indent=2, ensure_ascii=False), encoding='utf-8')
    print()
    print("raul_channels.json actualizado.")

if __name__ == '__main__':
    main()
