"""
Run once on M1 to generate permanent keys.
Stores keys in the local OS keyring and writes them to a file for USB transfer.

Usage:
    python3 generate_keys.py /path/to/usb/taskapp_keys.txt
"""
import sys
import secrets
import keyring

KEYRING_SVC = 'taskapp'

def main():
    if len(sys.argv) != 2:
        print('Usage: python3 generate_keys.py /path/to/usb/taskapp_keys.txt')
        sys.exit(1)

    output_path = sys.argv[1]

    # Check if keys already exist
    existing = keyring.get_password(KEYRING_SVC, 'encrypt_key')
    if existing:
        print('Keys already exist in keyring. Delete them first if you want to regenerate.')
        print('  keyring.delete_password("taskapp", "encrypt_key")')
        print('  keyring.delete_password("taskapp", "decrypt_key")')
        sys.exit(1)

    key_a = secrets.token_hex(32)  # M1 encrypt / M2 decrypt
    key_b = secrets.token_hex(32)  # M2 encrypt / M1 decrypt

    # Store in M1 keyring
    keyring.set_password(KEYRING_SVC, 'encrypt_key', key_a)
    keyring.set_password(KEYRING_SVC, 'decrypt_key', key_b)
    print('Keys stored in M1 keyring.')

    # Write to USB for M2 setup
    with open(output_path, 'w') as f:
        f.write(f'key_a={key_a}\n')
        f.write(f'key_b={key_b}\n')

    print(f'Key file written to: {output_path}')
    print()
    print('Next steps:')
    print('  1. Run load_keys.py on M2 using this file')
    print('  2. Shred the file from the USB immediately after:')
    print(f'     shred -u {output_path}')

if __name__ == '__main__':
    main()
