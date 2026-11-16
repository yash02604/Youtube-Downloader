#define AppName "Video Download Manager"
#define AppVersion "0.1.0"
#define AppExe "VideoDownloadManager.exe"

[Setup]
AppId={{CA293650-4D91-4C2A-A541-A8CF7BE20526}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\VideoDownloadManager
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
OutputDir=..\dist-installer
OutputBaseFilename=VideoDownloadManager-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
Uninstallable=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "..\dist\VideoDownloadManager\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
