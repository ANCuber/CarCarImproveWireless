from hm10_esp32 import HM10ESP32Bridge
import time
import sys
import threading

PORT = '/dev/ttyUSB1'
EXPECTED_NAME = 'HM10_Mega'
RETRY_INTERVAL = 1  # seconds between connection checks

def background_listener(bridge):
    while True:
        msg = bridge.listen()
        if msg:
            print(f"\r[HM10]: {msg}")
            print("You: ", end="", flush=True)
        time.sleep(0.1)

def main():
    bridge = HM10ESP32Bridge(port=PORT)
    
    # 1. Configuration Check
    current_name = bridge.get_hm10_name()
    if current_name != EXPECTED_NAME:
        print(f"Target mismatch. Current: {current_name}, Expected: {EXPECTED_NAME}")
        print(f"Updating target name to {EXPECTED_NAME}...")
        
        if bridge.set_hm10_name(EXPECTED_NAME):
            print("✅ Name updated successfully. Resetting ESP32...")
            bridge.reset()
            # Close old connection before re-init
            bridge.close()
            time.sleep(0.5)  # Give OS time to release the port
            bridge = HM10ESP32Bridge(port=PORT)
        else:
            print("❌ Failed to set name. Exiting.")
            sys.exit(1)

    # 2. Connection Check
    # The ESP32 keeps scanning for the HM-10 on its own; ask it once per
    # second until the link is up or the user presses Ctrl+C.
    try:
        last_status = None
        while True:
            status = bridge.get_status()
            if status == "CONNECTED":
                break
            if status != last_status:
                if status == "TIMEOUT":
                    print(f"⚠️ No reply from the ESP32 on {PORT}. Check PORT and the firmware.")
                else:
                    print(f"⚠️ ESP32 is {status}. Please ensure HM-10 is advertising.")
                print("Retrying every second (press Ctrl+C to quit)...")
                last_status = status
            time.sleep(RETRY_INTERVAL)
    except KeyboardInterrupt:
        bridge.close()
        print("\nStopped waiting. Exiting.")
        sys.exit(0)

    print(f"✨ Ready! Connected to {EXPECTED_NAME}")
    threading.Thread(target=background_listener, args=(bridge,), daemon=True).start()

    try:
        while True:
            user_msg = input("You: ")
            if user_msg.lower() in ['exit', 'quit']: break
            if user_msg: bridge.send(user_msg)
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        bridge.close()
    print("\nChat closed.")

if __name__ == "__main__":
    main()