#!/usr/bin/env python3
"""
Test script to validate Prestashop 1.6 Importer module structure
This simulates the validation that Odoo performs during module upgrade
"""

import os
import ast
import xml.etree.ElementTree as ET
import sys

def test_python_syntax(file_path):
    """Test Python file for syntax errors"""
    print(f"🔍 Testing Python syntax: {file_path}")
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        ast.parse(content)
        print(f"✅ Syntax OK: {file_path}")
        return True
    except SyntaxError as e:
        print(f"❌ Syntax Error in {file_path}: {e}")
        return False
    except Exception as e:
        print(f"❌ Error reading {file_path}: {e}")
        return False

def test_xml_syntax(file_path):
    """Test XML file for syntax errors"""
    print(f"🔍 Testing XML syntax: {file_path}")
    try:
        ET.parse(file_path)
        print(f"✅ XML OK: {file_path}")
        return True
    except ET.ParseError as e:
        print(f"❌ XML Parse Error in {file_path}: {e}")
        return False
    except Exception as e:
        print(f"❌ Error reading {file_path}: {e}")
        return False

def find_action_methods_in_xml(xml_file):
    """Extract action method names from XML views"""
    print(f"🔍 Extracting action methods from: {xml_file}")
    try:
        tree = ET.parse(xml_file)
        root = tree.getroot()
        actions = []
        
        # Find all buttons with name="action_*"
        for button in root.iter('button'):
            name = button.get('name')
            if name and name.startswith('action_'):
                actions.append(name)
                print(f"   📋 Found action: {name}")
        
        return actions
    except Exception as e:
        print(f"❌ Error parsing XML {xml_file}: {e}")
        return []

def find_methods_in_python(py_file):
    """Extract method names from Python file"""
    print(f"🔍 Extracting methods from: {py_file}")
    try:
        with open(py_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        tree = ast.parse(content)
        methods = []
        
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                methods.append(node.name)
                if node.name.startswith('action_'):
                    print(f"   🔧 Found action method: {node.name}")
        
        return methods
    except Exception as e:
        print(f"❌ Error parsing Python {py_file}: {e}")
        return []

def main():
    """Main test function"""
    print("🚀 PRESTASHOP 1.6 IMPORTER - MODULE VALIDATION TEST")
    print("=" * 60)
    
    base_dir = os.path.dirname(os.path.abspath(__file__))
    print(f"📁 Module directory: {base_dir}")
    
    errors = 0
    
    # Test 1: Python syntax
    print("\n📝 TEST 1: Python Syntax Validation")
    python_files = [
        '__init__.py',
        'models/__init__.py', 
        'models/prestashop_backend.py'
    ]
    
    for py_file in python_files:
        full_path = os.path.join(base_dir, py_file)
        if os.path.exists(full_path):
            if not test_python_syntax(full_path):
                errors += 1
        else:
            print(f"❌ Missing file: {py_file}")
            errors += 1
    
    # Test 2: XML syntax
    print("\n📄 TEST 2: XML Syntax Validation")
    xml_files = [
        'views/prestashop_backend_views.xml',
        'views/prestashop_importer_menu.xml'
    ]
    
    for xml_file in xml_files:
        full_path = os.path.join(base_dir, xml_file)
        if os.path.exists(full_path):
            if not test_xml_syntax(full_path):
                errors += 1
        else:
            print(f"❌ Missing file: {xml_file}")
            errors += 1
    
    # Test 3: Action method validation
    print("\n🔗 TEST 3: Action Method Validation")
    xml_actions = []
    py_methods = []
    
    # Get actions from XML
    xml_path = os.path.join(base_dir, 'views/prestashop_backend_views.xml')
    if os.path.exists(xml_path):
        xml_actions = find_action_methods_in_xml(xml_path)
    
    # Get methods from Python
    py_path = os.path.join(base_dir, 'models/prestashop_backend.py')
    if os.path.exists(py_path):
        py_methods = find_methods_in_python(py_path)
    
    # Check if all XML actions have corresponding Python methods
    print("\n🔍 Checking action method consistency:")
    missing_methods = []
    for action in xml_actions:
        if action in py_methods:
            print(f"   ✅ {action} - OK")
        else:
            print(f"   ❌ {action} - MISSING in Python file")
            missing_methods.append(action)
            errors += 1
    
    if missing_methods:
        print(f"\n❌ Missing methods: {missing_methods}")
    
    # Final result
    print("\n" + "=" * 60)
    if errors == 0:
        print("🎉 ALL TESTS PASSED! Module is ready for upgrade.")
        print("✅ No syntax errors found")
        print("✅ All XML actions have corresponding Python methods")
        print("✅ Module structure is valid")
        return 0
    else:
        print(f"❌ TESTS FAILED! Found {errors} error(s).")
        print("⚠️  Module upgrade may fail. Please fix the errors above.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
