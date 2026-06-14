import os
import re

def fix_file(path):
    with open(path, 'r') as f:
        content = f.read()
    
    # 1. Add 'from typing import Optional, Union, Any' if not present and we're going to use it
    # But simple regex might be enough if Optional is already imported.
    # Let's check imports first.
    
    # 2. Replace 'Type | None' with 'Optional[Type]'
    # We need to handle nested brackets like 'list[str] | None'
    
    # Pattern to match 'Something | None'
    # This is tricky because of 'list[str] | None'
    # Let's try to match the most common ones first.
    
    new_content = content
    
    # Handle 'Type | None' -> 'Optional[Type]'
    # We'll use a recursive-ish approach for nested brackets if needed, 
    # but for most files here it's simple.
    
    # Simple ones first:
    new_content = re.sub(r'([a-zA-Z0-9_]+) \| None', r'Optional[\1]', new_content)
    
    # Nested ones: 'list[...] | None'
    new_content = re.sub(r'(list\[[a-zA-Z0-9_]+\]) \| None', r'Optional[\1]', new_content)
    new_content = re.sub(r'(dict\[[a-zA-Z0-9_, ]+\]) \| None', r'Optional[\1]', new_content)
    new_content = re.sub(r'(tuple\[[a-zA-Z0-9_, ]+\]) \| None', r'Optional[\1]', new_content)
    
    # Handle 'float | float' or other '|' cases if any (though rare in this project)
    # The error 'TypeError: unsupported operand type(s) for |: 'types.GenericAlias' and 'NoneType''
    # specifically refers to 'list[str] | None' or similar.
    
    if new_content != content:
        # Ensure Optional is imported
        if 'from typing import' in new_content:
            if 'Optional' not in new_content:
                new_content = re.sub(r'from typing import ([a-zA-Z, ]+)', r'from typing import Optional, \1', new_content)
        elif 'import typing' in new_content:
            pass # already handled if using typing.Optional
        else:
            new_content = "from typing import Optional, Any, Union\n" + new_content
            
        with open(path, 'w') as f:
            f.write(new_content)
        return True
    return False

for root, dirs, files in os.walk('src/tw_quant_selector'):
    for file in files:
        if file.endswith('.py'):
            if fix_file(os.path.join(root, file)):
                print(f"Fixed {os.path.join(root, file)}")
