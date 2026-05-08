# Custom Templates

This directory contains Jinja2 template overrides for InvenioRDM.

## How It Works

Templates placed here override the default InvenioRDM templates.
The build process copies these into the Docker image.

## Structure

```
templates/
└── semantic-ui/
    └── invenio_app_rdm/
        ├── frontpage.html           # Homepage override
        ├── header.html              # Header override
        ├── footer.html              # Footer override
        └── records/
            └── detail.html          # Record detail page override
```

## Common Overrides

| Template | Purpose |
|----------|---------|
| `frontpage.html` | Homepage layout and content |
| `header.html` | Top navigation bar |
| `footer.html` | Footer content |
| `search.html` | Search results page |
| `records/detail.html` | Individual record display |

## Tips

- Copy the original template from the InvenioRDM source first
- Make your modifications
- Keep the same Jinja2 blocks structure
- Test locally before pushing

## Rebuild

Push changes to `main` branch to trigger automatic rebuild and deployment.
