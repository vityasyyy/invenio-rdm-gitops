# Custom Site Code

This directory contains custom Python modules for your InvenioRDM instance.

## Structure

```
site/
├── invenio_rdm_custom/          # Your custom Python package
│   ├── __init__.py
│   ├── ext.py                   # Flask extension entry point
│   ├── config.py                # Custom configuration overrides
│   └── views.py                 # Custom views/blueprints
└── setup.py                     # Package setup
```

## Usage

The custom site package is installed into the Docker image during build.
It can override or extend any Invenio functionality.

To add custom functionality:

1. Create a Python package under `site/`
2. Implement your custom code
3. Rebuild the Docker image: the CI pipeline will pick up changes automatically

## Example: Custom Theme

```python
# site/invenio_rdm_custom/config.py
from invenio_app_rdm.config import *

# Override theme colors
APP_THEME = ['semantic-ui']
THEME_ICONS = {'semantic-ui': 'default'}
```

## Example: Custom Views

```python
# site/invenio_rdm_custom/views.py
from flask import Blueprint

blueprint = Blueprint('custom', __name__)

@blueprint.route('/custom')
def custom_page():
    return "Hello from custom view!"
```

## Rebuild

Push changes to `main` branch to trigger automatic rebuild and deployment.
