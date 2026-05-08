# Custom Static Assets

This directory contains custom CSS, JavaScript, images, and other static files.

## How It Works

Files here are collected by Invenio during the `invenio collect` step
and served as static assets.

## Structure

```
static/
├── css/
│   └── custom-theme.css          # Custom styles
├── js/
│   └── custom.js                 # Custom JavaScript
├── images/
│   ├── logo.png                  # Custom logo
│   └── favicon.ico               # Custom favicon
└── fonts/
    └── custom-font.woff2         # Custom fonts
```

## Using Custom CSS

1. Add your CSS file: `static/css/custom-theme.css`
2. Reference it in a template override:
   ```html
   {% block css %}
   {{ super() }}
   <link rel="stylesheet" href="/static/css/custom-theme.css">
   {% endblock %}
   ```

## Using Custom Images

Images placed in `static/images/` are available at `/static/images/<filename>`.

## Rebuild

Push changes to `main` branch to trigger automatic rebuild and deployment.
