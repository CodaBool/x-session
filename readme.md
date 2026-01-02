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


# assumptions
- flatpak chrome
- already have a venv with the installed packages
- the above hidden files have been created
