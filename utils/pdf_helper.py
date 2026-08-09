import io
import re
import base64
from datetime import datetime
from html.parser import HTMLParser
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

class MemoBodyExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_body = False
        self.body_depth = 0
        self.body_html = []
        self.has_body = False

    def handle_starttag(self, tag, attrs):
        attr_dict = dict(attrs)
        tag = tag.lower()
        
        if not self.in_body:
            if tag == 'div' and 'memo-document-body' in attr_dict.get('class', ''):
                self.in_body = True
                self.body_depth = 1
                self.has_body = True
                return
        else:
            self.body_depth += 1
            attr_str = "".join(f' {k}="{v}"' for k, v in attrs)
            self.body_html.append(f"<{tag}{attr_str}>")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self.in_body:
            self.body_depth -= 1
            if self.body_depth == 0:
                self.in_body = False
                return
            self.body_html.append(f"</{tag}>")

    def handle_data(self, data):
        if self.in_body:
            escaped = data.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            self.body_html.append(escaped)

    def handle_entityref(self, name):
        if self.in_body:
            self.body_html.append(f"&{name};" )

def extract_memo_body(html_content):
    if not html_content:
        return ""
    extractor = MemoBodyExtractor()
    try:
        extractor.feed(html_content)
        if extractor.has_body:
            return "".join(extractor.body_html).strip()
    except Exception as e:
        print(f"Error extracting memo body: {e}")
    return html_content

class QuillHTMLToFlowablesParser(HTMLParser):
    def __init__(self, styles):
        super().__init__()
        self.styles = styles
        self.flowables = []
        self.current_text = ""
        self.current_style = styles['Normal']
        self.list_type = None  # 'ul' or 'ol'
        self.list_index = 0
        self.skip_depth = 0
        self.allowed_inline_tags = {'b', 'i', 'u', 'strong', 'em', 'font', 'a', 'br'}

    def handle_starttag(self, tag, attrs):
        attr_dict = dict(attrs)
        tag = tag.lower()
        align = attr_dict.get('class', '')
        
        # Check if we should skip this tag (restricted bands)
        if self.skip_depth > 0 or 'memo-restricted-band' in align:
            self.skip_depth += 1
            return
        
        if tag in ['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'div']:
            self.flush_paragraph()
            
            if tag.startswith('h'):
                self.current_style = self.styles.get(tag.upper(), self.styles['Heading2'])
            elif tag == 'li':
                self.current_style = self.styles['Normal']
                if self.list_type == 'ol':
                    self.current_text = f"{self.list_index}. "
                    self.list_index += 1
                else:
                    self.current_text = "&bull; "
            else:
                self.current_style = self.styles['Normal']
                if 'ql-align-center' in align:
                    self.current_style = self.styles['CenterAlign']
                elif 'ql-align-right' in align:
                    self.current_style = self.styles['RightAlign']
                elif 'ql-align-justify' in align:
                    self.current_style = self.styles['JustifyAlign']
                    
        elif tag == 'ul':
            self.flush_paragraph()
            self.list_type = 'ul'
            
        elif tag == 'ol':
            self.flush_paragraph()
            self.list_type = 'ol'
            self.list_index = 1
            
        elif tag in self.allowed_inline_tags:
            mapped_tag = tag
            if tag == 'strong': mapped_tag = 'b'
            elif tag == 'em': mapped_tag = 'i'
            
            attr_str = ""
            for k, v in attrs:
                if tag == 'a' and k == 'href':
                    attr_str += f' href="{v}"'
                elif tag == 'font' and k in ['color', 'face', 'size']:
                    attr_str += f' {k}="{v}"'
            
            self.current_text += f"<{mapped_tag}{attr_str}>"

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self.skip_depth > 0:
            self.skip_depth -= 1
            return
            
        if tag in ['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'div']:
            self.flush_paragraph()
        elif tag in ['ul', 'ol']:
            self.flush_paragraph()
            self.list_type = None
        elif tag in self.allowed_inline_tags:
            mapped_tag = tag
            if tag == 'strong': mapped_tag = 'b'
            elif tag == 'em': mapped_tag = 'i'
            self.current_text += f"</{mapped_tag}>"

    def handle_data(self, data):
        if self.skip_depth > 0:
            return
        escaped_data = data.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        self.current_text += escaped_data

    def handle_entityref(self, name):
        if self.skip_depth > 0:
            return
        self.current_text += f"&{name};"

    def flush_paragraph(self):
        text = self.current_text.strip()
        if text:
            try:
                self.flowables.append(Paragraph(text, self.current_style))
                self.flowables.append(Spacer(1, 8))
            except Exception:
                clean_text = re.sub(r'<[^>]+>', '', text)
                self.flowables.append(Paragraph(clean_text, self.current_style))
                self.flowables.append(Spacer(1, 8))
            self.current_text = ""

def generate_letterhead_pdf(reference_number, sender, subject, content_html, last_sig, date_str=None):
    pdf_buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        pdf_buffer,
        pagesize=A4,
        leftMargin=54,
        rightMargin=54,
        topMargin=54,
        bottomMargin=54
    )
    
    if not date_str:
        date_str = datetime.now().strftime("%d %B %Y")
        
    styles = getSampleStyleSheet()
    
    normal_style = styles['Normal']
    normal_style.fontSize = 11
    normal_style.leading = 15
    normal_style.textColor = colors.HexColor('#000000')
    
    styles.add(ParagraphStyle(
        'RightAlign',
        parent=normal_style,
        alignment=2
    ))
    styles.add(ParagraphStyle(
        'CenterAlign',
        parent=normal_style,
        alignment=1
    ))
    styles.add(ParagraphStyle(
        'JustifyAlign',
        parent=normal_style,
        alignment=4
    ))
    
    story = []
    
    # 1. Spacer for physical letterhead margin (approx 2.2 inches / 160 points)
    story.append(Spacer(1, 160))
    
    # 2. Subject Line
    subject_p = Paragraph(f"<b><u>SUBJECT: {subject.upper()}</u></b>", styles['CenterAlign'])
    story.append(subject_p)
    story.append(Spacer(1, 20))
    
    # 3. Body Content
    parser = QuillHTMLToFlowablesParser(styles)
    parser.feed(content_html)
    story.extend(parser.flowables)
    
    # 4. Spacer for physical signature line
    story.append(Spacer(1, 50))
    
    doc.build(story)
    pdf_buffer.seek(0)
    return pdf_buffer.getvalue()
