# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec для NEXUS v1.1.1 (Linux/macOS).

Сборка:
  pyinstaller nexus.spec --clean --noconfirm

Результат:
  dist/nexus/nexus      ← исполняемый
  dist/nexus/_internal/ ← библиотеки
"""
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_dynamic_libs


block_cipher = None


hiddenimports = [
    # NEXUS package
    "nexus", "nexus.core", "nexus.deps", "nexus.crypto",
    "nexus.storage", "nexus.domain", "nexus.security",
    "nexus.sandbox", "nexus.qr", "nexus.integrations",
    "nexus.app", "nexus.app_pages", "nexus.nexus_splash",
    # PRO
    "nexus_qr_pro", "nexus_code_pro", "nexus_debugger",
    "nexus_ctk_patch",
    # QR
    "qrcode", "qrcode.image", "qrcode.image.pil",
    "qrcode.image.styledpil", "qrcode.image.styles",
    "qrcode.image.styles.moduledrawers",
    "qrcode.image.styles.colormasks", "qrcode.image.svg",
    "qrcode.constants", "qrcode.main", "qrcode.util",
    # PIL
    "PIL", "PIL.Image", "PIL.ImageDraw", "PIL.ImageFilter",
    "PIL.ImageFont", "PIL.ImageTk", "PIL.ImageOps",
    "PIL.ImageGrab", "PIL._tkinter_finder", "PIL._imagingtk",
    # customtkinter
    "customtkinter",
    # crypto
    "cryptography", "cryptography.fernet",
    "cryptography.hazmat.primitives.ciphers.aead",
    "cryptography.hazmat.primitives.kdf.pbkdf2",
    "cryptography.hazmat.primitives.hashes",
    # argon2
    "argon2",
    # pyzbar
    "pyzbar", "pyzbar.pyzbar",
    # stdlib
    "sqlite3", "tkinter", "tkinter.filedialog",
    "tkinter.messagebox", "tkinter.font", "tkinter.ttk",
    "email.mime.text", "email.mime.multipart", "smtplib",
    "urllib.request", "http.server", "socketserver",
    "ipaddress", "webbrowser", "zipfile", "csv", "json",
    "hashlib", "hmac", "secrets", "base64", "uuid",
    "locale", "platform", "subprocess", "tempfile", "shutil",
]

hiddenimports += collect_submodules("customtkinter")
hiddenimports += collect_submodules("cryptography")
hiddenimports += collect_submodules("qrcode")
hiddenimports += collect_submodules("argon2")
hiddenimports += collect_submodules("pyzbar")


datas = []
datas += collect_data_files("customtkinter")
datas += collect_data_files("qrcode")

binaries = []
binaries += collect_dynamic_libs("argon2")
binaries += collect_dynamic_libs("pyzbar")
binaries += collect_dynamic_libs("cryptography")


excludes = [
    "matplotlib", "numpy", "pandas", "scipy", "sympy",
    "IPython", "jupyter", "notebook", "nbconvert",
    "pytest", "unittest", "test", "tests",
    "flask", "django", "fastapi", "uvicorn", "aiohttp",
    "streamlit", "altair", "pyarrow",
    "pygame", "torch", "tensorflow", "keras",
    "tkinter.test", "tkinter.dnd", "tkinter.tix",
    "setuptools", "pip", "wheel", "distutils", "lib2to3",
]


a = Analysis(
    ["nexus/__main__.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="nexus",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="nexus",
)