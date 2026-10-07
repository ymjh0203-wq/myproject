' ============================================================
'  주문관리 - 바탕화면 아이콘(바로가기) 만들기
'  - 이 파일을 한 번 더블클릭하면, 예쁜 아이콘이 입혀진
'    '주문관리' 바로가기를 바탕화면에 만듭니다.
'  - 그 아이콘을 더블클릭하면 데스크톱 앱 창이 바로 뜹니다.
'  - 아이콘을 작업표시줄에 '고정'해두면 늘 그 아이콘으로 열 수 있어요.
' ============================================================

Set fso = CreateObject("Scripting.FileSystemObject")
appDir = fso.GetParentFolderName(WScript.ScriptFullName)
Set sh = CreateObject("WScript.Shell")
desktop = sh.SpecialFolders("Desktop")

iconPath = appDir & "\assets\app_icon.ico"
pyw = appDir & "\.venv\Scripts\pythonw.exe"

If Not fso.FileExists(iconPath) Then
    MsgBox "아이콘 파일을 찾을 수 없습니다: " & iconPath, 48, "주문관리"
    WScript.Quit
End If
If Not fso.FileExists(pyw) Then
    MsgBox ".venv\Scripts\pythonw.exe 를 찾을 수 없습니다. 프로그램 폴더 안에서 실행하세요.", 48, "주문관리"
    WScript.Quit
End If

Set lnk = sh.CreateShortcut(desktop & "\주문관리.lnk")
lnk.TargetPath = pyw
lnk.Arguments = "desktop_app.py"
lnk.WorkingDirectory = appDir
lnk.IconLocation = iconPath
lnk.Description = "주문관리"
lnk.WindowStyle = 1
lnk.Save

MsgBox "바탕화면에 '주문관리' 아이콘을 만들었어요!" & vbCrLf & _
       "그 아이콘을 더블클릭하면 창이 뜨고, 작업표시줄에 고정해서 쓰시면 됩니다.", _
       64, "주문관리"
