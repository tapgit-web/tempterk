import os
import sys
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfgen import canvas

class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super(NumberedCanvas, self).__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super(NumberedCanvas, self).showPage()
        super(NumberedCanvas, self).save()

    def draw_page_decorations(self, page_count):
        self.saveState()
        self.setFont("Helvetica", 9)
        self.setFillColor(colors.HexColor("#64748B"))
        
        # Header (pages 2+)
        if self._pageNumber > 1:
            self.drawString(54, 750, "TempTrek Environmental Monitoring System v2.5.5 — GAMP 5 Classification Guide")
            self.setStrokeColor(colors.HexColor("#CBD5E1"))
            self.setLineWidth(0.5)
            self.line(54, 742, 558, 742)
            
        # Footer (all pages)
        self.setStrokeColor(colors.HexColor("#CBD5E1"))
        self.setLineWidth(0.5)
        self.line(54, 50, 558, 50)
        
        self.drawString(54, 35, "CONFIDENTIAL & GxP REGULATORY — TempTrek Systems")
        page_str = f"Page {self._pageNumber} of {page_count}"
        self.drawRightString(558, 35, page_str)
        self.restoreState()

def create_gamp_pdf(output_path):
    doc = SimpleDocTemplate(
        output_path,
        pagesize=letter,
        leftMargin=54,
        rightMargin=54,
        topMargin=54,
        bottomMargin=54
    )
    
    styles = getSampleStyleSheet()
    c_primary = colors.HexColor("#0F172A")
    c_secondary = colors.HexColor("#1E3A8A")
    c_accent = colors.HexColor("#2563EB")
    c_dark = colors.HexColor("#0F172A")
    c_light_bg = colors.HexColor("#F8FAFC")
    
    title_style = ParagraphStyle(
        'DocTitle', parent=styles['Heading1'], fontName='Helvetica-Bold', fontSize=22, leading=26, textColor=c_primary, spaceAfter=6
    )
    subtitle_style = ParagraphStyle(
        'DocSubtitle', parent=styles['Normal'], fontName='Helvetica', fontSize=11, leading=15, textColor=c_accent, spaceAfter=16
    )
    h1_style = ParagraphStyle(
        'SectionH1', parent=styles['Heading1'], fontName='Helvetica-Bold', fontSize=14, leading=17, textColor=c_secondary, spaceBefore=12, spaceAfter=6, keepWithNext=True
    )
    h2_style = ParagraphStyle(
        'SectionH2', parent=styles['Heading2'], fontName='Helvetica-Bold', fontSize=11, leading=14, textColor=c_dark, spaceBefore=8, spaceAfter=4, keepWithNext=True
    )
    body_style = ParagraphStyle(
        'BodyTextCustom', parent=styles['BodyText'], fontName='Helvetica', fontSize=9.5, leading=13.5, textColor=c_dark, spaceAfter=5
    )
    bullet_style = ParagraphStyle(
        'BulletCustom', parent=body_style, leftIndent=12, firstLineIndent=-8, spaceAfter=3
    )

    # --- High Contrast Table Styles ---
    th_style = ParagraphStyle(
        'TableHeader', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=9.5, leading=13, textColor=colors.HexColor("#FFFFFF")
    )
    tb_style = ParagraphStyle(
        'TableBody', parent=styles['Normal'], fontName='Helvetica', fontSize=9.5, leading=13.5, textColor=colors.HexColor("#0F172A")
    )
    tb_bold_style = ParagraphStyle(
        'TableBodyBold', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=9.5, leading=13.5, textColor=colors.HexColor("#0F172A")
    )

    story = []
    
    # Title
    story.append(Paragraph("TempTrek Environmental Monitoring System", title_style))
    story.append(Paragraph("GAMP 5 Software Classification & Computer System Validation (CSV) Guide", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=2, color=c_accent, spaceBefore=0, spaceAfter=12))
    
    # Metadata
    meta_data = [
        [Paragraph("System Name:", tb_bold_style), Paragraph("TempTrek EMS v2.5.5", tb_style),
         Paragraph("Primary Category:", tb_bold_style), Paragraph("GAMP 5 Category 4 (Configured)", tb_style)],
        [Paragraph("Regulatory Scope:", tb_bold_style), Paragraph("FDA 21 CFR Part 11 / EU Annex 11", tb_style),
         Paragraph("Secondary Category:", tb_bold_style), Paragraph("GAMP 5 Category 5 (Custom)", tb_style)],
        [Paragraph("Target Industry:", tb_bold_style), Paragraph("Pharma / Biotech / GxP Facilities", tb_style),
         Paragraph("Validation Status:", tb_bold_style), Paragraph("CSV Ready (OQ Test Suite Pass)", tb_style)]
    ]
    t_meta = Table(meta_data, colWidths=[100, 150, 110, 144])
    t_meta.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), c_light_bg),
        ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E1")),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor("#E2E8F0")),
        ('PADDING', (0,0), (-1,-1), 5),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))
    story.append(t_meta)
    story.append(Spacer(1, 10))

    # Section 1: Overview
    story.append(Paragraph("1. GAMP 5 Framework Overview", h1_style))
    story.append(Paragraph("The ISPE GAMP 5 framework (Good Automated Manufacturing Practice, Second Edition) provides a risk-based approach to compliant computer system validation accepted by the US FDA, EMA, and international health authorities.", body_style))

    # Section 2: Categories
    story.append(Paragraph("2. Software Category Classification", h1_style))
    story.append(Paragraph("TempTrek is classified under two operational GAMP 5 categories depending on site deployment:", body_style))
    
    cat_table = [
        [Paragraph("GAMP Category", th_style), Paragraph("Software Definition", th_style), Paragraph("TempTrek Application & Rationale", th_style)],
        [Paragraph("Category 4<br/>(Configured)", tb_bold_style), Paragraph("Commercial Off-The-Shelf (COTS) software configured to fit business processes without modifying code.", tb_style), Paragraph("<b>Standard Deployment:</b> Administrators configure Modbus TCP devices (IP, Port, Slave ID), register mappings (FC01-FC04), alarm thresholds, and user access roles via standard UI forms.", tb_style)],
        [Paragraph("Category 5<br/>(Custom/Bespoke)", tb_bold_style), Paragraph("Custom software written from scratch or customized for specific customer requirements.", tb_style), Paragraph("<b>Custom Development:</b> Applies if in-house Python source code (<code>2.py</code>, <code>storage_security.py</code>) is modified or extended for custom hardware protocols.", tb_style)]
    ]
    t_cat = Table(cat_table, colWidths=[100, 190, 214])
    t_cat.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), c_secondary),
        ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E1")),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor("#E2E8F0")),
        ('PADDING', (0,0), (-1,-1), 5),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
    ]))
    story.append(t_cat)
    story.append(Spacer(1, 10))

    # Section 3: 21 CFR Part 11
    story.append(Paragraph("3. FDA 21 CFR Part 11 & EU Annex 11 Compliance Features", h1_style))
    story.append(Paragraph("• <b>Chained Audit Trail:</b> Every user action, register change, and historical data review is recorded with chained SHA-256 hashes, timestamps, and workstation IDs.", bullet_style))
    story.append(Paragraph("• <b>Access Controls:</b> Salted PBKDF2-SHA256 password hashing, mandatory password change policy, 6+ character length enforcement, and 90-day password expiration.", bullet_style))
    story.append(Paragraph("• <b>Storage Protection:</b> Windows DPAPI configuration encryption, exclusive Windows Administrators / SYSTEM backup ACLs (`%ProgramData%\\TempTrekBackups`), and AES-GCM encrypted `.ttbackup` archives.", bullet_style))
    story.append(Paragraph("• <b>Electronic Signatures & Approvals:</b> Password-authenticated data review workflows. PDF exports automatically watermark unapproved historical readings as <i>DRAFT</i>.", bullet_style))

    story.append(Spacer(1, 10))

    # Section 4: CSV Lifecycle
    story.append(Paragraph("4. Computer System Validation (CSV) Deliverables", h1_style))
    story.append(Paragraph("1. <b>User Requirement Specification (URS):</b> Defines continuous monitoring, alarm notification, and 21 CFR Part 11 audit requirements.", bullet_style))
    story.append(Paragraph("2. <b>Functional Specification (FS) / Configuration Spec (CS):</b> Documents system architecture, Modbus register mappings, and alarm thresholds.", bullet_style))
    story.append(Paragraph("3. <b>Installation Qualification (IQ):</b> Verifies host Windows OS prerequisites, administrative UAC rights, and application binary provisioning.", bullet_style))
    story.append(Paragraph("4. <b>Operational Qualification (OQ):</b> Executes the 50-test automated regression suite (`python -m unittest discover -s tempterk -p \"test_*.py\" -v`).", bullet_style))
    story.append(Paragraph("5. <b>Performance Qualification (PQ):</b> Demonstrates continuous 24 to 72-hour sensor acquisition and alert dispatch in real operational conditions.", bullet_style))

    story.append(Spacer(1, 12))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#CBD5E1"), spaceBefore=10, spaceAfter=10))
    story.append(Paragraph("<b>TempTrek Quality & Regulatory Assurance Division</b> | GxP Validation Package", ParagraphStyle('FooterText', parent=body_style, fontSize=8, textColor=colors.HexColor("#64748B"), alignment=1)))

    doc.build(story, canvasmaker=NumberedCanvas)
    print(f"Successfully generated GAMP guide PDF at: {output_path}")

if __name__ == "__main__":
    out_file = os.path.join("d:\\TAP\\tempterk\\tempterk\\docs", "TempTrek_GAMP_Validation_Guide.pdf")
    create_gamp_pdf(out_file)
