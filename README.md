# site/

Static files for GitHub Pages.

Publish this directory after generating fresh data:

```bash
python3 ../scripts/export_usage.py
```

Safe to publish:
- `index.html`
- `app.js`
- `styles.css`
- `data/summary.json`

Do not publish anything from `../data/private/`.
