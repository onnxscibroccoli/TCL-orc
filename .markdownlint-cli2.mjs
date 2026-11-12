// File selection belongs to the workflow or CLI arguments.
// No implicit repo-wide glob: historical docs debt is tracked separately.
export default {
  ignores: [
    "node_modules",
    "build",
    "_site"
  ],

  config: {
    "default": true,
    "MD013": false,
    "MD024": {
      "siblings_only": true
    },
    "MD029": {
      "style": "ordered"
    },
    "MD033": {
      "allowed_elements": ["sub", "sup", "span", "div", "details", "summary"]
    },
    "MD041": {
      "level": 1
    }
  }
};
