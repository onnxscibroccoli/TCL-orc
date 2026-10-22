const fs = require('fs');
const path = require('path');
const katex = require('katex');

const docsDir = path.join(__dirname, '../../docs');
let errorCount = 0;

function getMarkdownFiles(dir) {
    if (!fs.existsSync(dir)) return [];
    let results = [];
    const list = fs.readdirSync(dir);
    list.forEach(file => {
        const filePath = path.join(dir, file);
        const stat = fs.statSync(filePath);
        if (stat && stat.isDirectory()) {
            results = results.concat(getMarkdownFiles(filePath));
        } else if (file.endsWith('.md')) {
            results.push(filePath);
        }
    });
    return results;
}

function validateMathInFile(filePath) {
    const content = fs.readFileSync(filePath, 'utf8');
    const mathRegex = /\$\$([\s\S]+?)\$\$|\$([^\$\n]+)\$/g;
    let match;

    while ((match = mathRegex.exec(content)) !== null) {
        const formula = match[1] || match[2];
        const isDisplayMode = Boolean(match[1]);
        try {
            katex.renderToString(formula, {
                displayMode: isDisplayMode,
                throwOnError: true
            });
        } catch (err) {
            console.error(`KaTeX Parsing Error in ${filePath}:`);
            console.error(`Formula: ${formula}`);
            console.error(`Message: ${err.message}\n`);
            errorCount++;
        }
    }
}

const files = getMarkdownFiles(docsDir);
if (files.length === 0) {
    console.log('No Markdown files found under docs/. Skipping KaTeX validation.');
    process.exit(0);
}
files.forEach(validateMathInFile);

if (errorCount > 0) {
    console.error(`KaTeX Validation Failed with ${errorCount} error(s).`);
    process.exit(1);
} else {
    console.log('All mathematical expressions successfully validated with KaTeX.');
}
