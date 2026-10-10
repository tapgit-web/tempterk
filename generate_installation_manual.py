import os
import sys
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether, HRFlowable
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
            self.drawString(54, 750, "TempTrek Environmental Monitoring System v2.5.5 — Installation Manual")
            self.setStrokeColor(colors.HexColor("#CBD5E1"))
            self.setLineWidth(0.5)
            self.line(54, 742, 558, 742)
            
        # Footer (all pages)
        self.setStrokeColor(colors.HexColor("#CBD5E1"))
        self.setLineWidth(0.5)
        self.line(54, 50, 558, 50)
        
        self.drawString(54, 35, "CONFIDENTIAL & PROPRIETARY — TempTrek Systems")
        page_str = f"Page {self._pageNumber} of {page_count}"
        self.drawRightString(558, 35, page_str)
        self.restoreState()

def create_installation_manual(output_path):
    doc = SimpleDocTemplate(
        output_path,
        pagesize=letter,
        leftMargin=54,
        rightMargin=54,
        topMargin=54,
        bottomMargin=54
    )
    
    styles = getSampleStyleSheet()
    
    # Custom colors
    c_primary = colors.HexColor("#0F172A")    # Dark slate
    c_secondary = colors.HexColor("#1E3A8A")  # Deep navy
    c_accent = colors.HexColor("#2563EB")     # Bright blue
    c_dark = colors.HexColor("#0F172A")       # High contrast dark text
    c_light_bg = colors.HexColor("#F8FAFC")   # Soft grey
    
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=24,
        leading=28,
        textColor=c_primary,
        spaceAfter=6
    )
    
    subtitle_style = ParagraphStyle(
        'DocSubtitle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=12,
        leading=16,
        textColor=c_accent,
        spaceAfter=20
    )
    
    h1_style = ParagraphStyle(
        'SectionH1',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=15,
        leading=18,
        textColor=c_secondary,
        spaceBefore=14,
        spaceAfter=8,
        keepWithNext=True
    )

    h2_style = ParagraphStyle(
        'SectionH2',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=12,
        leading=15,
        textColor=c_dark,
        spaceBefore=10,
        spaceAfter=4,
        keepWithNext=True
    )
    
    body_style = ParagraphStyle(
        'BodyTextCustom',
        parent=styles['BodyText'],
        fontName='Helvetica',
        fontSize=10,
        leading=14,
        textColor=c_dark,
        spaceAfter=6
    )

    bullet_style = ParagraphStyle(
        'BulletCustom',
        parent=body_style,
        leftIndent=15,
        firstLineIndent=-10,
        spaceAfter=4
    )

    code_style = ParagraphStyle(
        'CodeStyle',
        parent=styles['Normal'],
        fontName='Courier',
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#0F172A"),
        backColor=colors.HexColor("#F1F5F9"),
        borderColor=colors.HexColor("#CBD5E1"),
        borderWidth=0.5,
        borderPadding=6,
        spaceBefore=4,
        spaceAfter=6
    )

    callout_style = ParagraphStyle(
        'CalloutText',
        parent=body_style,
        fontSize=9.5,
        leading=13.5,
        textColor=colors.HexColor("#1E293B")
    )

    # --- High Contrast Table Cell Styles ---
    th_style = ParagraphStyle(
        'TableHeader',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=10,
        leading=13,
        textColor=colors.HexColor("#FFFFFF")  # Pure White Text for dark header rows
    )

    tb_style = ParagraphStyle(
        'TableBody',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9.5,
        leading=13.5,
        textColor=colors.HexColor("#0F172A")  # High Contrast Dark Text for body cells
    )

    tb_bold_style = ParagraphStyle(
        'TableBodyBold',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=9.5,
        leading=13.5,
        textColor=colors.HexColor("#0F172A")  # High Contrast Dark Bold Text
    )

    story = []
    
    # --- Title Banner ---
    story.append(Paragraph("TempTrek Environmental Monitoring System", title_style))
    story.append(Paragraph("Version 2.5.5 — Official Installation & Deployment Guide", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=2, color=c_accent, spaceBefore=0, spaceAfter=14))
    
    # --- Document Metadata Box ---
    meta_data = [
        [Paragraph("Document Type:", tb_bold_style), Paragraph("Installation & Administration Manual", tb_style),
         Paragraph("Target Platform:", tb_bold_style), Paragraph("Windows 10 / 11 / Server (x64)", tb_style)],
        [Paragraph("App Version:", tb_bold_style), Paragraph("v2.5.5", tb_style),
         Paragraph("Security Standard:", tb_bold_style), Paragraph("DPAPI / AES-GCM / 21 CFR Part 11", tb_style)],
        [Paragraph("GAMP 5 Category:", tb_bold_style), Paragraph("Category 4 (Configured) / Category 5", tb_style),
         Paragraph("Privilege Level:", tb_bold_style), Paragraph("Windows Administrator (UAC Required)", tb_style)]
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
    story.append(Spacer(1, 14))
    
    # --- Section 1: Introduction & System Requirements ---
    story.append(Paragraph("1. System Requirements & Prerequisites", h1_style))
    story.append(Paragraph("Before installing TempTrek Environmental Monitoring System, verify that the host machine meets the following minimum requirements:", body_style))
    
    req_data = [
        [Paragraph("Component", th_style), Paragraph("Minimum Specification", th_style), Paragraph("Recommended Specification", th_style)],
        [Paragraph("Operating System", tb_bold_style), Paragraph("Windows 10 x64 (Build 19041+)", tb_style), Paragraph("Windows 11 x64 / Windows Server 2022", tb_style)],
        [Paragraph("Architecture", tb_bold_style), Paragraph("x86-64 (64-bit)", tb_style), Paragraph("x86-64 (64-bit)", tb_style)],
        [Paragraph("Memory (RAM)", tb_bold_style), Paragraph("2 GB RAM", tb_style), Paragraph("4 GB RAM or higher", tb_style)],
        [Paragraph("Disk Space", tb_bold_style), Paragraph("250 MB available storage", tb_style), Paragraph("2 GB+ (for multi-year CSV logs & backups)", tb_style)],
        [Paragraph("User Privileges", tb_bold_style), Paragraph("Local Administrator rights (UAC)", tb_style), Paragraph("Local Administrator rights (UAC)", tb_style)],
        [Paragraph("Network Interface", tb_bold_style), Paragraph("Ethernet/Wi-Fi with LAN access", tb_style), Paragraph("Dedicated Gigabit LAN for Modbus TCP", tb_style)],
        [Paragraph("Dependencies", tb_bold_style), Paragraph("Bundled in MSI/EXE (No external runtime required)", tb_style), Paragraph("Python 3.12 (Only if compiling from source)", tb_style)]
    ]
    t_req = Table(req_data, colWidths=[110, 194, 200])
    t_req.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), c_secondary),
        ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E1")),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor("#E2E8F0")),
        ('PADDING', (0,0), (-1,-1), 6),
    ]))
    story.append(t_req)
    story.append(Spacer(1, 14))

    # --- Section 2: Distribution Packages ---
    story.append(Paragraph("2. Distribution Packages", h1_style))
    story.append(Paragraph("TempTrek is distributed in two primary Windows deployment formats located in the <code>dist/</code> directory:", body_style))
    
    story.append(Paragraph("• <b>Native Windows Installer (<code>TempTrek-2.5.5-x64.msi</code>):</b> Per-machine installation package. Installs to 64-bit <code>Program Files</code>, creates Start Menu and Desktop shortcuts, and supports standard Windows Apps uninstall & upgrade management.", bullet_style))
    story.append(Paragraph("• <b>Standalone Executable (<code>TempTrek Environmental Monitoring System.exe</code>):</b> Portable executable containing embedded Python runtime and all security/Modbus dependencies. Requires no installation step.", bullet_style))
    story.append(Paragraph("• <b>Inno Setup Executable (<code>TempTrek Environmental Monitoring System_Setup.exe</code>):</b> Alternative setup wizard compiled via <code>installer.iss</code>.", bullet_style))
    
    story.append(Spacer(1, 10))

    # --- Section 3: Step-by-Step Installation Procedure ---
    story.append(Paragraph("3. Step-by-Step Installation Procedure", h1_style))
    
    story.append(Paragraph("Method A: Installing via Native Windows Installer (.MSI)", h2_style))
    story.append(Paragraph("1. Locate <b><code>dist/TempTrek-2.5.5-x64.msi</code></b>.", bullet_style))
    story.append(Paragraph("2. Right-click the file and select <b>Install</b> or double-click to launch the Windows Installer setup wizard.", bullet_style))
    story.append(Paragraph("3. Accept the Windows User Account Control (UAC) prompt to allow administrative elevated access.", bullet_style))
    story.append(Paragraph("4. The installer automatically provisions binary files into <code>C:\\Program Files\\TempTrek Environmental Monitoring System</code>.", bullet_style))
    story.append(Paragraph("5. Shortcuts will automatically be created on the <b>Desktop</b> and in the <b>Start Menu</b> under <i>TempTrek</i>.", bullet_style))

    story.append(Spacer(1, 6))
    story.append(Paragraph("Method B: Deploying Standalone Executable (.EXE)", h2_style))
    story.append(Paragraph("1. Copy <b><code>dist/TempTrek Environmental Monitoring System.exe</code></b> to your preferred application directory (e.g. <code>C:\\Program Files\\TempTrek\\</code>).", bullet_style))
    story.append(Paragraph("2. Right-click the executable, select <b>Properties</b>, go to <b>Compatibility</b>, and check <b>Run this program as an administrator</b> (or accept the built-in PyInstaller manifest UAC elevation prompt).", bullet_style))
    story.append(Paragraph("3. Double-click the <code>.exe</code> to launch the system.", bullet_style))

    story.append(Spacer(1, 10))

    # --- Section 4: First-Time Setup & Security Configuration ---
    story.append(Paragraph("4. First-Time Launch & Security Setup", h1_style))
    
    callout_data = [[
        Paragraph("<b>IMPORTANT SECURITY REQUIREMENT:</b> TempTrek requires execution under a Windows Administrator account. Backup storage and directory ACL management rely on elevated SYSTEM privileges. Launch the application using <i>Run as Administrator</i>.", callout_style)
    ]]
    t_callout = Table(callout_data, colWidths=[504])
    t_callout.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#FEF3C7")),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor("#F59E0B")),
        ('PADDING', (0,0), (-1,-1), 8),
    ]))
    story.append(t_callout)
    story.append(Spacer(1, 10))

    story.append(Paragraph("Initial Super Admin Setup", h2_style))
    story.append(Paragraph("Upon launching the system for the first time on a machine:", body_style))
    story.append(Paragraph("1. The application detects that no active user database exists in <code>%APPDATA%\\TempTrek\\config\\users.json</code>.", bullet_style))
    story.append(Paragraph("2. A <b>First-Time Super Admin Password Prompt</b> will appear.", bullet_style))
    story.append(Paragraph("3. Enter a master password fulfilling the security policy:", bullet_style))
    story.append(Paragraph("&nbsp;&nbsp;&nbsp;&nbsp;• Minimum length: <b>6 characters</b>", bullet_style))
    story.append(Paragraph("&nbsp;&nbsp;&nbsp;&nbsp;• Must be a new password different from existing credentials.", bullet_style))
    story.append(Paragraph("4. The password is hashed using <b>PBKDF2-SHA256</b> with unique cryptographic salting.", bullet_style))

    story.append(Spacer(1, 6))
    story.append(Paragraph("Application Storage & DPAPI Encryption", h2_style))
    story.append(Paragraph("TempTrek stores application state and configuration securely across Windows directories:", body_style))
    
    dir_data = [
        [Paragraph("Directory Path", th_style), Paragraph("Purpose & Security Rules", th_style)],
        [Paragraph("<code>%APPDATA%\\TempTrek\\config\\</code>", tb_bold_style), Paragraph("Stores <code>devices.json</code>, <code>users.json</code>, <code>email_settings.json</code>, and <code>trend_settings.json</code>. Protected via <b>Windows DPAPI</b> encryption.", tb_style)],
        [Paragraph("<code>%APPDATA%\\TempTrek\\live\\</code>", tb_bold_style), Paragraph("Stores daily-rotated CSV logs (<code>[IP]_[Port]_s[SlaveID]_[YYYY-MM-DD].csv</code>) and alarm history.", tb_style)],
        [Paragraph("<code>%APPDATA%\\TempTrek\\reports\\</code>", tb_bold_style), Paragraph("Destination for generated ReportLab PDF exports and approval audit records.", tb_style)],
        [Paragraph("<code>%ProgramData%\\TempTrekBackups\\</code>", tb_bold_style), Paragraph("Protected backup location. Access restricted exclusively to <b>Windows Administrators / SYSTEM</b> via strict ACLs. Holds password-encrypted <code>.ttbackup</code> archives.", tb_style)]
    ]
    t_dir = Table(dir_data, colWidths=[210, 294])
    t_dir.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), c_secondary),
        ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E1")),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor("#E2E8F0")),
        ('PADDING', (0,0), (-1,-1), 6),
    ]))
    story.append(t_dir)
    
    story.append(Spacer(1, 14))

    # --- Section 5: Device & Monitoring Setup ---
    story.append(Paragraph("5. Modbus TCP & Device Configuration", h1_style))
    story.append(Paragraph("After initial login, configure target Modbus TCP devices under <b>Administration > Node Config</b>:", body_style))
    
    story.append(Paragraph("1. <b>Add Modbus Slave Node:</b> Enter Device IP, Port (default <code>502</code>), Slave ID (e.g. <code>1</code>), and <b>Time Interval to Store (sec)</b>.", bullet_style))
    story.append(Paragraph("2. <b>Configure Registers:</b> Click <i>Register Mapping</i> to map channels:", bullet_style))
    story.append(Paragraph("&nbsp;&nbsp;&nbsp;&nbsp;• <b>Output Coil (FC01)</b> / <b>Input Coil (FC02)</b>: Boolean states (e.g. 0 = Normal, 1 = Alarm).", bullet_style))
    story.append(Paragraph("&nbsp;&nbsp;&nbsp;&nbsp;• <b>Holding Register (FC03)</b> / <b>Input Register (FC04)</b>: Numeric parameters (FLOAT32, INT16, INT32, BCD).", bullet_style))
    story.append(Paragraph("3. <b>Decimal Precision:</b> Set scaling precision per channel to automatically format live cards and PDF trend graphs.", bullet_style))
    story.append(Paragraph("4. <b>Start Monitoring:</b> Click <b>START MONITORING</b> to initiate 1.0-second background polling threads.", bullet_style))

    story.append(Spacer(1, 10))

    # --- Section 6: Backup & Restoration ---
    story.append(Paragraph("6. Backup & Disaster Recovery", h1_style))
    story.append(Paragraph("Navigate to <b>Administration > Backup & Restore</b> in an elevated Administrator session:", body_style))
    story.append(Paragraph("• <b>Backup Password Setup:</b> Click <i>SAVE SETTINGS</i> or <i>CREATE BACKUP NOW</i> to set a dedicated backup password.", bullet_style))
    story.append(Paragraph("• <b>Backup Encryption:</b> Backups produce <code>.ttbackup</code> zip archives encrypted with AES-GCM.", bullet_style))
    story.append(Paragraph("• <b>Restoration Safety:</b> Restoring an archive automatically freezes polling, validates checksums, creates a pre-restore safety snapshot, executes atomic transactional folder replacement with recovery journal rollback, and restarts the application.", bullet_style))

    story.append(Spacer(1, 10))

    # --- Section 7: Source Compilation ---
    story.append(Paragraph("7. Rebuilding Packages from Source", h1_style))
    story.append(Paragraph("To rebuild distribution packages from Python source code:", body_style))
    story.append(Paragraph("1. Install Windows Python 3.12 (required for <code>msilib</code> support).", bullet_style))
    story.append(Paragraph("2. Install required build packages:", bullet_style))
    story.append(Paragraph("<code>pip install pyinstaller cryptography>=46,<51 reportlab Pillow requests pymodbus certifi</code>", code_style))
    story.append(Paragraph("3. Execute the automated master build script from project root:", bullet_style))
    story.append(Paragraph("<code>build.bat</code>", code_style))
    story.append(Paragraph("This generates both <code>dist/TempTrek Environmental Monitoring System.exe</code> and <code>dist/TempTrek-2.5.5-x64.msi</code>.", bullet_style))

    story.append(Spacer(1, 14))

    # --- Section 8: Verification & Support ---
    story.append(Paragraph("8. Verification & Test Suite Execution", h1_style))
    story.append(Paragraph("To verify application integrity and security controls on a host system, run the automated test suite:", body_style))
    story.append(Paragraph("<code>python -m unittest discover -s tempterk -p \"test_*.py\" -v</code>", code_style))
    story.append(Paragraph("All 50 unit tests must pass, confirming DPAPI security, salted PBKDF2 hashing, atomic write protection, ACL generation, and restore rollback mechanics.", body_style))

    story.append(Spacer(1, 14))

    # --- Section 9: GAMP 5 Classification & Compliance ---
    story.append(Paragraph("9. GAMP 5 Software Classification & GxP Compliance", h1_style))
    story.append(Paragraph("Under the ISPE GAMP 5 framework (Good Automated Manufacturing Practice, Second Edition) for pharmaceutical and regulated GxP environments:", body_style))
    
    gamp_table_data = [
        [Paragraph("GAMP Category", th_style), Paragraph("Deployment Model & Classification Justification", th_style)],
        [Paragraph("Category 4<br/>(Configured Software)", tb_bold_style), Paragraph("<b>Standard Production Deployment:</b> TempTrek is delivered as a pre-compiled software package (MSI/EXE). Site administrators configure Modbus device nodes, register mappings (FC01-FC04), threshold alarm rules, and user roles via standard UI forms without modifying source code.", tb_style)],
        [Paragraph("Category 5<br/>(Custom / Bespoke Software)", tb_bold_style), Paragraph("<b>Custom / Source Code Modification:</b> Applies if the underlying Python codebase (<code>2.py</code>, <code>storage_security.py</code>) is modified, extended, or customized for site-specific custom hardware protocols.", tb_style)]
    ]
    t_gamp = Table(gamp_table_data, colWidths=[140, 364])
    t_gamp.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), c_secondary),
        ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E1")),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor("#E2E8F0")),
        ('PADDING', (0,0), (-1,-1), 6),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
    ]))
    story.append(t_gamp)
    story.append(Spacer(1, 8))
    
    story.append(Paragraph("FDA 21 CFR Part 11 & EU Annex 11 Compliance Summary:", h2_style))
    story.append(Paragraph("• <b>Audit Trail</b>: Comprehensive, chained SHA-256 audit logging of user actions, configuration changes, and viewed data.", bullet_style))
    story.append(Paragraph("• <b>Security & Integrity</b>: Windows DPAPI configuration encryption, PBKDF2 password hashing, and restricted administrator-only backup storage ACLs.", bullet_style))
    story.append(Paragraph("• <b>Report Signatures</b>: Password-authenticated data approvals; unapproved historical logs render reports with a <i>DRAFT</i> watermark.", bullet_style))

    story.append(Spacer(1, 14))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#CBD5E1"), spaceBefore=10, spaceAfter=10))
    story.append(Paragraph("<b>TempTrek Technical Support & Documentation</b> | Customer Support & Licensing Division", ParagraphStyle('FooterText', parent=body_style, fontSize=8, textColor=colors.HexColor("#64748B"), alignment=1)))

    doc.build(story, canvasmaker=NumberedCanvas)
    print(f"Successfully generated installation manual PDF at: {output_path}")

if __name__ == "__main__":
    out_file = os.path.join("d:\\TAP\\tempterk\\tempterk", "TempTrek_Installation_Manual.pdf")
    create_installation_manual(out_file)
