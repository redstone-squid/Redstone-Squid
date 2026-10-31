#!/data/data/com.termux/files/usr/bin/sh
# Keep libpython in DT_NEEDED: Android cannot resolve Python symbols from the host,
# and Termux's --as-needed otherwise drops PyO3's link request.
exec cc "$@" -Wl,--no-as-needed -lpython3 -Wl,--as-needed
