# SOMA — Boot Configuration
# This runs before main.py on every Pico W startup

import gc
gc.collect()

# Disable debug REPL on UART (frees UART for other use if needed)
# import os
# os.dupterm(None, 1)
