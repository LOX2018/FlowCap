@ECHO OFF
SET SRC=C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2\dist\DYAutoDM_v2_0.10.0.exe
SET DST=C:\temp\dyautodm_test\DYAutoDM_v2_0.10.0.exe
COPY /Y "%SRC%" "%DST%"
IF EXIST "%DST%" (ECHO COPIED_OK) ELSE (ECHO COPY_FAILED)
