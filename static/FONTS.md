# Bundled fonts

These WOFF2 files are self-hosted (rather than loaded from a third-party CDN) so the
app makes no external font request — every page load stays first-party, which matters
for a PHI application. Streamlit serves them at `app/static/*` via
`server.enableStaticServing` (see `.streamlit/config.toml`); the UI falls back to the
system sans / mono stack if a face fails to load.

| Family | Weights | Source | License |
|--------|---------|--------|---------|
| **Inter** | 400, 500, 600, 700 | [rsms/inter](https://github.com/rsms/inter) (via [@fontsource/inter](https://www.npmjs.com/package/@fontsource/inter)) | [SIL Open Font License 1.1](https://openfontlicense.org/) |
| **JetBrains Mono** | 400, 500 | [JetBrains/JetBrainsMono](https://github.com/JetBrains/JetBrainsMono) (via [@fontsource/jetbrains-mono](https://www.npmjs.com/package/@fontsource/jetbrains-mono)) | [SIL Open Font License 1.1](https://openfontlicense.org/) |

Both fonts are licensed under the SIL OFL 1.1, which permits bundling and
redistribution with this attribution. The full license text ships with each font's
upstream source linked above. Files here are the Latin subset only.
