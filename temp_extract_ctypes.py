import urllib.request
import re

url = 'https://docs.python.org/3/library/ctypes.html'
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
html = urllib.request.urlopen(req).read().decode('utf-8')

# Find the main content area
body_pattern = r'<div class="body">(.+?)</div>\s*</div>\s*</div>\s*</div>\s*</main>'
body_match = re.search(body_pattern, html, re.DOTALL)

if body_match:
    content = body_match.group(1)
else:
    # Fallback: get everything between <main> tags
    main_pattern = r'<main>(.+?)</main>'
    main_match = re.search(main_pattern, html, re.DOTALL)
    content = main_match.group(1) if main_match else html

# Remove scripts and styles
content = re.sub(r'<script\b[^<]*(?:(?!<script>)<[^<]*)*</script>', '', content, flags=re.DOTALL)
content = re.sub(r'<style\b[^<]*(?:(?!<style>)<[^<]*)*</style>', '', content, flags=re.DOTALL)

# Remove HTML tags
text = re.sub(r'<[^>]+>', '\n', content)

# Clean up whitespace
lines = [line.strip() for line in text.split('\n')]
text = '\n'.join(line for line in lines if line)

# Save
output_path = 'E:/VSCodeProjects/Nexus/input_docs/raw/ctypes_full.txt'
with open(output_path, 'w', encoding='utf-8') as f:
    f.write(text)

print(f'Saved {len(text)} chars to {output_path}')
