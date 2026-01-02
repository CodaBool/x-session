# X session tokens
uses your browser and a credential file to generate session tokens for x.

Used in my self-host of RSSHub and Nitter

A `automated.sh` script handles the overall process as well as copying over the files to my server


# hidden files
- .env (x credentials)
  - formatted like this:
```
USER1=
PASS1=
TOTP1=
USER2=
PASS2=
TOTP2=
```
- .rsshub.env (the environment file I'll replace the auth token in and transfer)

# Python carriage issue
the `nodriver` has a carriage issue. Simply append `# -*- coding: latin-1 -*-` to the top of the file found under `./venv/lib64/python3.14/site-packages/nodriver/cdp/network.py`

# assumptions
- linux flatpak chrome (if not on that platform change the session.py script)
- already have a venv with the installed packages
- the above hidden files have been created
