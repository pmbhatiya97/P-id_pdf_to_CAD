#define MyAppName "LineaForge"
#define MyAppVersion "0.1.0"
[Setup]
AppId={{9242569F-41ED-4CCE-83C1-DBEE59962D4F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=LineaForge Works
DefaultDirName={localappdata}\Programs\LineaForge
DefaultGroupName=LineaForge
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=LineaForge-Setup-0.1.0
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\LineaForge.exe
[Files]
Source: "..\dist\LineaForge\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\LineaForge"; Filename: "{app}\LineaForge.exe"
Name: "{autodesktop}\LineaForge"; Filename: "{app}\LineaForge.exe"; Tasks: desktopicon
[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; Flags: unchecked
[Run]
Filename: "{app}\LineaForge.exe"; Description: "Open LineaForge"; Flags: nowait postinstall skipifsilent
