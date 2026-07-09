# invoke SourceDir generated makefile for mss_mmw.per4f
mss_mmw.per4f: .libraries,mss_mmw.per4f
.libraries,mss_mmw.per4f: package/cfg/mss_mmw_per4f.xdl
	$(MAKE) -f C:\Users\pc\Desktop\vital_sign\firmware\vital_signs_tracking_mss/src/makefile.libs

clean::
	$(MAKE) -f C:\Users\pc\Desktop\vital_sign\firmware\vital_signs_tracking_mss/src/makefile.libs clean

