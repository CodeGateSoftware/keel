; Inno Setup script for the keel Windows installer (#438).
;
; Compiled by the release workflow's desktop job (and by the dispatch-only
; .github/workflows/installer-smoke.yml) with the version and arch passed as defines:
;
;     ISCC.exe /DKeelVersion=0.12.0 /DKeelArch=x86_64 packaging\keel.iss
;
; The #ifndef defaults exist so the script still compiles when opened directly in Inno's
; compiler GUI; a real release always passes the real values, and the smoke workflow
; passes a placeholder -- so a compile failure is caught before the release dispatch,
; never during one.
#ifndef KeelVersion
  #define KeelVersion "0.0.0"
#endif
#ifndef KeelArch
  #define KeelArch "x86_64"
#endif

; THE PROGRAM, NOT THE DEPLOYMENT. #438 separates two locations and the installer must
; keep them separate:
;
;   program     this directory ({localappdata}\Programs\keel): keel.exe and the bundled
;               runtime. Replaced wholesale on every install.
;   deployment  {localappdata}\keel: config.yaml, keel*.db, .env, logs/. Created by the
;               app, NEVER by this installer -- "config.yaml is never overwritten by an
;               installer" and "no database is ever replaced, moved, or migrated by the
;               installer" are #438's hard rules. This is also why there is no
;               [UninstallDelete] section: uninstalling must leave the deployment intact.
;
; NOT HERE YET, DELIBERATELY: the install-over-existing-keel UX #438 specifies (read the
; installed version from on-disk metadata -- never execute it; update on newer; confirm
; on same version; confirm with a migrations-do-not-reverse warning on downgrade, because
; keel/data/db.py has no down-migrations). That needs [Code] against the on-disk build
; info and was deferred with the rest of the installer UX; this script is the packaging
; and signing vehicle that #438's workflow work needed first.

[Setup]
; A fixed AppId is how Inno recognizes a previous install of the SAME app for upgrades
; and uninstall entries. It must never change across versions.
AppId={{6B1C9D2E-8E4A-4F0B-9A17-2D5C0E4F8A31}
AppName=keel
AppVersion={#KeelVersion}
AppPublisher=CodeGateSoftware
AppPublisherURL=https://github.com/CodeGateSoftware/keel
AppUpdatesURL=https://github.com/CodeGateSoftware/keel/releases
; PER-USER, NO ADMIN PROMPT (#438): `lowest` installs without an elevation dialog, and
; {localappdata}\Programs is the per-user location Windows itself uses for per-user
; application installs. An admin prompt on a tool that then asks for exchange API keys
; is a security habit this project refuses to teach.
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\keel
; [Files] Source paths and OutputDir are relative to SourceDir, i.e. the repository root
; (this script lives in packaging/). Both workflows invoke ISCC from the root.
SourceDir=..
OutputDir=out
OutputBaseFilename=keel-{#KeelVersion}-windows-{#KeelArch}-setup
; A signed log under %TEMP% for every install: support asks "what did the installer do"
; exactly once per user, and this is the answer that needs no reproducing.
SetupLogging=yes
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
UninstallDisplayName=keel

[Files]
; The whole PyInstaller --onedir tree: keel.exe plus the bundled interpreter and its
; native extensions. #438 chose --onedir over --onefile for faster start, simpler
; per-binary signing for notarisation-class tooling, and a lower AV/SmartScreen
; false-positive rate. `ignoreversion` because the whole program directory is replaced
; on every install -- the deployment is not in here (see the header).
Source: "dist\keel\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
; Per-user Start Menu shortcut only ({userprograms} writes HKCU, no elevation). Windows
; allocates a console on launch, which is the designed Windows experience (#438): no
; wrapper is needed there, unlike macOS where the .app must launch `keel serve`.
Name: "{userprograms}\keel"; Filename: "{app}\keel.exe"
