# Open the trading dashboard on your iPad

## Important
The trading engine is written in Python. iPadOS does not run this Python project as a normal iPad app. The intended way to use it on an iPad is to host it online and open the resulting private/public web-app URL in Safari.

## Free-first setup

1. On your iPad, open Safari and go to https://github.com/ and sign in (or create an account).
2. Create a **new private repository** called `multi-market-paper-trader`.
3. Extract the ZIP on your iPad using the Files app. If iPadOS does not offer extraction, tap the ZIP in Files; it should create a folder.
4. In the GitHub repository, choose **Add file → Upload files** and upload the contents of the extracted folder. Make sure `app.py`, `requirements.txt`, `README.md`, and the `.streamlit` folder are included. GitHub's web upload may make hidden folders awkward; if so, use a computer for this one-time upload.
5. Open https://share.streamlit.io/ and sign in with GitHub.
6. Choose **Create app**, select the repository, branch, and `app.py`, then deploy.
7. When deployment finishes, open the Streamlit app URL in Safari on your iPad.
8. Optional: in Safari, tap **Share → Add to Home Screen** to create an icon for quick access.

## What runs where
- Safari on the iPad displays and controls the dashboard.
- Streamlit Community Cloud runs the Python app.
- The app downloads market history when you press **Run market scan & paper trade**.

## Free-hosting caveats
Free hosting can sleep, restart, impose quotas, or change its terms. This prototype uses a local SQLite database; storage on a free hosted service may not be durable across restarts or redeployments. Do not rely on it as a permanent trading ledger. Export/backup functionality and durable database hosting should be added before extended testing.

## Simulation only
This app does not place real orders and does not connect to a broker. It uses daily historical market data and simplified assumptions. It is not a continuously running 24/7 trading service: a scan runs only when you press the button. It does not guarantee profits or cap losses in real markets.
