# govstack

*Analysis pending...*

## Facts

| | |
|---|---|
| **Stack** | JavaScript, Python + GitHub Actions, React, Tailwind CSS |
| **Test** | `pytest` |
| **Build** | `npm run build` |
| **Dev** | `npm run dev` |

## Module Boundaries

| Module | Imports | Cannot Import |
|--------|---------|---------------|
| `apps` | config | civicos-site, static |
| `civicos-site` |  | apps, config, static |
| `config` | apps | civicos-site, static |
| `static` |  | apps, civicos-site, config |

## Build & Test

```bash
pytest
npm run build
npm run dev
```

