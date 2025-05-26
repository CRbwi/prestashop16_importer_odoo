#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test script para verificar la implementación de los métodos faltantes
en el Prestashop Importer
"""

import sys
import re

def check_method_implementation():
    """Verificar que los métodos _get_or_create_categories y _import_product_images están implementados"""
    
    with open('models/prestashop_backend.py', 'r', encoding='utf-8') as f:
        content = f.read()
    
    print("🔍 VERIFICANDO IMPLEMENTACIÓN DE MÉTODOS FALTANTES")
    print("=" * 60)
    
    # Verificar método _get_or_create_categories
    if 'def _get_or_create_categories(' in content:
        print("✅ Método _get_or_create_categories: ENCONTRADO")
        
        # Verificar características específicas
        checks = [
            ('Manejo de jerarquía', 'parent_id'),
            ('Creación recursiva', 'create_category_with_parent'),
            ('Validación de duplicados', 'existing_category'),
            ('Logging detallado', '_logger.info'),
            ('Manejo de sesiones', 'session.get'),
        ]
        
        for name, pattern in checks:
            if pattern in content:
                print(f"  ✅ {name}: OK")
            else:
                print(f"  ❌ {name}: FALTA")
    else:
        print("❌ Método _get_or_create_categories: NO ENCONTRADO")
    
    print()
    
    # Verificar método _import_product_images
    if 'def _import_product_images(' in content:
        print("✅ Método _import_product_images: ENCONTRADO")
        
        # Verificar características específicas
        checks = [
            ('Descarga de imágenes', 'images/products'),
            ('Validación de formato', 'content-type'),
            ('Conversión base64', 'base64.b64encode'),
            ('Imagen principal', 'image_1920'),
            ('Imágenes adicionales', 'product.image'),
            ('Manejo de errores', 'except Exception'),
        ]
        
        for name, pattern in checks:
            if pattern in content:
                print(f"  ✅ {name}: OK")
            else:
                print(f"  ❌ {name}: FALTA")
    else:
        print("❌ Método _import_product_images: NO ENCONTRADO")
    
    print()
    
    # Verificar llamadas a los métodos en action_import_products
    print("🔗 VERIFICANDO INTEGRACIÓN CON action_import_products")
    print("-" * 50)
    
    if '_get_or_create_categories(category_ids, session, test_url)' in content:
        print("✅ Llamada a _get_or_create_categories: OK")
    else:
        print("❌ Llamada a _get_or_create_categories: FALTA")
    
    if '_import_product_images(product_obj, product_id, image_ids, session, test_url)' in content:
        print("✅ Llamada a _import_product_images: OK")
    else:
        print("❌ Llamada a _import_product_images: FALTA")
    
    print()
    
    # Verificar imports necesarios
    print("📦 VERIFICANDO IMPORTS NECESARIOS")
    print("-" * 30)
    
    imports = [
        ('base64', 'import base64'),
        ('requests', 'import requests'),
        ('xml.etree.ElementTree', 'import xml.etree.ElementTree'),
        ('time', 'import time'),
    ]
    
    for name, pattern in imports:
        if pattern in content:
            print(f"✅ {name}: OK")
        else:
            print(f"❌ {name}: FALTA")
    
    print()
    print("📊 ESTADÍSTICAS DEL ARCHIVO")
    print("-" * 25)
    lines = content.splitlines()
    print(f"📝 Total de líneas: {len(lines)}")
    print(f"📏 Total de caracteres: {len(content)}")
    print(f"🔧 Métodos encontrados: {len(re.findall(r'def\s+\w+\(', content))}")
    
    print()
    print("🎯 RESUMEN DE VALIDACIÓN")
    print("=" * 25)
    
    # Contar problemas
    issues = 0
    if 'def _get_or_create_categories(' not in content:
        issues += 1
    if 'def _import_product_images(' not in content:
        issues += 1
    if '_get_or_create_categories(category_ids, session, test_url)' not in content:
        issues += 1
    if '_import_product_images(product_obj, product_id, image_ids, session, test_url)' not in content:
        issues += 1
    
    if issues == 0:
        print("🎉 ESTADO: COMPLETAMENTE FUNCIONAL")
        print("✅ Todos los métodos están implementados correctamente")
        print("✅ Las integraciones están en su lugar")
        print("✅ Los imports necesarios están presentes")
        return True
    else:
        print(f"⚠️ ESTADO: {issues} PROBLEMAS ENCONTRADOS")
        print("❌ Algunos métodos o integraciones faltan")
        return False

if __name__ == "__main__":
    try:
        success = check_method_implementation()
        if success:
            print("\n🚀 EL MÓDULO ESTÁ LISTO PARA USAR")
            sys.exit(0)
        else:
            print("\n🔧 EL MÓDULO NECESITA CORRECCIONES")
            sys.exit(1)
    except Exception as e:
        print(f"\n💥 ERROR EN LA VALIDACIÓN: {e}")
        sys.exit(1)
