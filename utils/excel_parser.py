# utils/excel_parser.py
import zipfile
import xml.etree.ElementTree as ET

def parse_xlsx(file_stream):
    """
    Parses a standard .xlsx Excel file into a list of row lists.
    Uses only standard python library components to avoid external dependencies.
    """
    with zipfile.ZipFile(file_stream) as z:
        # 1. Parse shared strings list
        shared_strings = []
        try:
            with z.open('xl/sharedStrings.xml') as f:
                tree = ET.parse(f)
                root = tree.getroot()
                ns = {'ns': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
                for si in root.findall('.//ns:si', ns):
                    # Join text values for potential rich text element fragments
                    t_elems = si.findall('.//ns:t', ns)
                    text = "".join([t.text or "" for t in t_elems])
                    shared_strings.append(text)
        except KeyError:
            # Shared strings file is optional if sheet contains only numeric values
            pass

        # 2. Parse sheet1.xml worksheets data
        with z.open('xl/worksheets/sheet1.xml') as f:
            tree = ET.parse(f)
            root = tree.getroot()
            ns = {'ns': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}

            rows_data = []
            for row_elem in root.findall('.//ns:row', ns):
                row_num_str = row_elem.get('r')
                if not row_num_str:
                    continue
                row_num = int(row_num_str)
                row_cells = {}
                for cell_elem in row_elem.findall('ns:c', ns):
                    cell_ref = cell_elem.get('r')
                    if not cell_ref:
                        continue
                    # Extract column letters (A, B, AA, etc.)
                    col_letter = "".join([char for char in cell_ref if char.isalpha()]).upper()
                    
                    val = ""
                    t_attr = cell_elem.get('t')
                    if t_attr == 's':
                        # Shared string index
                        v_elem = cell_elem.find('ns:v', ns)
                        if v_elem is not None and v_elem.text:
                            try:
                                idx = int(v_elem.text)
                                val = shared_strings[idx]
                            except (ValueError, IndexError):
                                val = v_elem.text
                    elif t_attr == 'inlineStr':
                        is_elem = cell_elem.find('.//ns:t', ns)
                        if is_elem is not None:
                            val = is_elem.text or ""
                    else:
                        v_elem = cell_elem.find('ns:v', ns)
                        if v_elem is not None:
                            val = v_elem.text or ""
                    
                    row_cells[col_letter] = val.strip()
                rows_data.append((row_num, row_cells))

            # Ensure rows are sorted sequentially by row number
            rows_data.sort(key=lambda x: x[0])

            # Find all unique column codes
            all_cols = set()
            for r_num, cells in rows_data:
                all_cols.update(cells.keys())

            # Convert column letters to numbers for correct sorting order (e.g. Z before AA)
            def col_to_num(col):
                num = 0
                for c in col:
                    num = num * 26 + (ord(c) - ord('A') + 1)
                return num

            sorted_cols = sorted(list(all_cols), key=col_to_num)

            # Build matrix of rows and columns
            formatted_rows = []
            for r_num, cells in rows_data:
                row_list = []
                for col in sorted_cols:
                    row_list.append(cells.get(col, ""))
                formatted_rows.append(row_list)
            
            return formatted_rows


def parse_xlsx_to_dicts(file_stream):
    """
    Parses .xlsx file and maps rows to dictionaries using normalized column headers.
    Returns a list of dicts.
    """
    rows = parse_xlsx(file_stream)
    if not rows:
        return []
        
    raw_headers = [str(h).strip().lower() for h in rows[0]]
    
    # Header normalization mapping to support different variations
    header_mapping = {
        "firstname": "firstname", "first name": "firstname", "first_name": "firstname",
        "surname": "surname", "last name": "surname", "lastname": "surname", "last_name": "surname",
        "middlename": "middlename", "middle name": "middlename", "middle_name": "middlename",
        "category": "category",
        "role": "role", "user role": "role", "user_role": "role", "position": "role",
        "directorate": "directorate", "dept": "directorate", "department": "directorate",
        "service number": "service_number", "service_number": "service_number", "sn": "service_number", "service no": "service_number", "service_no": "service_number",
        "personal email": "alternate_email", "personal_email": "alternate_email", "alternate email": "alternate_email", "alternate_email": "alternate_email", "email": "alternate_email", "alternate email address": "alternate_email", "personal email address": "alternate_email",
        "official email": "official_email", "official_email": "official_email", "official email address": "official_email", "official_email_address": "official_email"
    }
    
    normalized_headers = []
    for raw in raw_headers:
        norm = header_mapping.get(raw, raw.replace(" ", "_"))
        normalized_headers.append(norm)

    parsed_records = []
    for r in rows[1:]:
        # Skip empty rows
        if not any(r):
            continue
            
        record = {}
        for idx, header in enumerate(normalized_headers):
            if header:
                val = r[idx] if idx < len(r) else ""
                record[header] = str(val).strip()
        parsed_records.append(record)
        
    return parsed_records
