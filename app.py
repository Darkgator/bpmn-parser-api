from flask import Flask, request, jsonify
from flask_cors import CORS
import xml.etree.ElementTree as ET
import traceback

app = Flask(__name__)
CORS(app)

def parse_bpmn(file_content):
    """Parseia arquivo BPMN 2.0 e extrai informações estruturadas"""
    
    ns = {
        'bpmn': 'http://www.omg.org/spec/BPMN/20100524/MODEL',
        'bpmndi': 'http://www.omg.org/spec/BPMN/20100524/DI',
        'dc': 'http://www.omg.org/spec/DD/20100524/DC',
        'di': 'http://www.omg.org/spec/DD/20100524/DI'
    }
    
    try:
        root = ET.fromstring(file_content)
    except Exception as e:
        return {'error': f'Erro ao ler arquivo: {str(e)}'}
    
    result = {
        'title': '',
        'objective': '',
        'elements': {},
        'flows': {},
        'lanes': {},
        'data_stores': {},
        'annotations': [],
        'flow_order': [],
        'warnings': []
    }
    
    # Buscar processos e escolher o com mais elementos
    processes = root.findall('.//bpmn:process', ns)
    
    if not processes:
        result['error'] = 'Nenhum elemento <process> encontrado'
        return result
    
    process = None
    max_elements = 0
    
    for proc in processes:
        num_elements = len([
            elem for elem in proc.iter() 
            if any(keyword in elem.tag for keyword in ['task', 'event', 'gateway', 'sequenceFlow'])
        ])
        
        if num_elements > max_elements:
            max_elements = num_elements
            process = proc
    
    if process is None:
        result['error'] = 'Nenhum processo com elementos encontrado'
        return result
    
    # Extrair título e objetivo
    result['title'] = process.get('name', '')
    
    doc = process.find('bpmn:documentation', ns)
    if doc is not None and doc.text:
        result['objective'] = doc.text.strip()
    
    # Mapear elementos por ID
    for elem in process.iter():
        elem_id = elem.get('id')
        if elem_id:
            tag = elem.tag.split('}')[-1]
            result['elements'][elem_id] = {
                'id': elem_id,
                'type': tag,
                'name': elem.get('name', ''),
                'incoming': [],
                'outgoing': []
            }
    
    # Preencher incoming/outgoing
    for elem_id, elem_data in result['elements'].items():
        elem = process.find(f".//*[@id='{elem_id}']", ns)
        if elem is not None:
            for inc in elem.findall('bpmn:incoming', ns):
                if inc.text:
                    elem_data['incoming'].append(inc.text)
            for out in elem.findall('bpmn:outgoing', ns):
                if out.text:
                    elem_data['outgoing'].append(out.text)
    
    # Mapear lanes (atores)
    lane_set = process.find('bpmn:laneSet', ns)
    if lane_set is not None:
        for lane in lane_set.findall('bpmn:lane', ns):
            lane_name = lane.get('name', '')
            for flow_node_ref in lane.findall('bpmn:flowNodeRef', ns):
                activity_id = flow_node_ref.text
                if activity_id:
                    result['lanes'][activity_id] = lane_name
    
    # Mapear sequence flows
    for seq_flow in process.findall('.//bpmn:sequenceFlow', ns):
        flow_id = seq_flow.get('id')
        source = seq_flow.get('sourceRef')
        target = seq_flow.get('targetRef')
        flow_name = seq_flow.get('name', '')
        
        condition_elem = seq_flow.find('bpmn:conditionExpression', ns)
        condition = condition_elem.text if condition_elem is not None and condition_elem.text else ''
        
        result['flows'][flow_id] = {
            'id': flow_id,
            'source': source,
            'target': target,
            'name': flow_name,
            'condition': condition
        }
    
    # Mapear data stores
    for data_store in root.findall('.//bpmn:dataStoreReference', ns):
        ds_id = data_store.get('id')
        ds_name = data_store.get('name', '')
        result['data_stores'][ds_id] = ds_name
    
    # Associações de data
    for elem_id, elem_data in result['elements'].items():
        elem = process.find(f".//*[@id='{elem_id}']", ns)
        if elem is not None:
            systems = []
            
            for data_input in elem.findall('bpmn:dataInputAssociation', ns):
                source_ref = data_input.find('bpmn:sourceRef', ns)
                if source_ref is not None and source_ref.text in result['data_stores']:
                    systems.append(result['data_stores'][source_ref.text])
            
            for data_output in elem.findall('bpmn:dataOutputAssociation', ns):
                target_ref = data_output.find('bpmn:targetRef', ns)
                if target_ref is not None and target_ref.text in result['data_stores']:
                    systems.append(result['data_stores'][target_ref.text])
            
            if systems:
                elem_data['systems'] = list(set(systems))
    
    # Extrair anotações
    annotations_map = {}
    for annotation in root.findall('.//bpmn:textAnnotation', ns):
        ann_id = annotation.get('id')
        text_elem = annotation.find('bpmn:text', ns)
        if text_elem is not None and text_elem.text:
            annotations_map[ann_id] = text_elem.text.strip()
    
    for association in root.findall('.//bpmn:association', ns):
        source = association.get('sourceRef')
        target = association.get('targetRef')
        
        if source in annotations_map:
            linked_name = result['elements'].get(target, {}).get('name', target)
            result['annotations'].append({
                'text': annotations_map[source],
                'linked_to': linked_name,
                'linked_id': target
            })
    
    # Construir ordem cronológica
    start_events = [eid for eid, e in result['elements'].items() if e['type'] == 'startEvent']
    
    if not start_events:
        result['warnings'].append('Nenhum evento de início encontrado')
        return result
    
    visited = set()
    flow_order = []
    
    def traverse(elem_id, level=0):
        if elem_id in visited or elem_id not in result['elements']:
            return
        
        visited.add(elem_id)
        elem = result['elements'][elem_id]
        
        flow_item = {
            'id': elem_id,
            'type': elem['type'],
            'name': elem['name'],
            'actor': result['lanes'].get(elem_id, ''),
            'systems': elem.get('systems', []),
            'level': level,
            'outgoing_flows': []
        }
        
        if 'gateway' in elem['type'].lower():
            is_divergent = len(elem['outgoing']) > 1
            is_convergent = len(elem['incoming']) > 1
            
            flow_item['is_divergent'] = is_divergent
            flow_item['is_convergent'] = is_convergent
            
            if is_divergent:
                for out_flow_id in elem['outgoing']:
                    if out_flow_id in result['flows']:
                        flow_info = result['flows'][out_flow_id]
                        flow_item['outgoing_flows'].append({
                            'name': flow_info['name'],
                            'condition': flow_info['condition'],
                            'target': flow_info['target']
                        })
        
        flow_order.append(flow_item)
        
        for out_flow_id in elem['outgoing']:
            if out_flow_id in result['flows']:
                next_id = result['flows'][out_flow_id]['target']
                
                if next_id in visited:
                    for idx, item in enumerate(flow_order):
                        if item['id'] == next_id:
                            flow_item['loop_to'] = idx + 1
                            break
                else:
                    next_level = level + 1 if flow_item.get('is_divergent') else level
                    traverse(next_id, next_level)
    
    traverse(start_events[0])
    result['flow_order'] = flow_order
    
    # Gerar avisos
    for elem_id, elem in result['elements'].items():
        if elem['type'] in ['task', 'userTask', 'serviceTask', 'manualTask', 'scriptTask', 'sendTask', 'receiveTask']:
            if not elem['name']:
                result['warnings'].append(f"Atividade '{elem_id}' sem nome definido")
            
            if result['lanes'] and elem_id not in result['lanes']:
                result['warnings'].append(f"Atividade '{elem['name'] or elem_id}' sem ator identificado")
        
        if 'gateway' in elem['type'].lower() and not elem['name']:
            result['warnings'].append(f"Gateway '{elem_id}' sem nome definido")
    
    return result

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'healthy'}), 200

@app.route('/parse-bpmn', methods=['POST'])
def parse_bpmn_endpoint():
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'Nenhum arquivo enviado'}), 400
        
        file = request.files['file']
        
        if file.filename == '':
            return jsonify({'error': 'Arquivo vazio'}), 400
        
        if not file.filename.endswith('.bpmn'):
            return jsonify({'error': 'Arquivo deve ser .bpmn'}), 400
        
        file_content = file.read()
        result = parse_bpmn(file_content)
        
        return jsonify(result), 200
    
    except Exception as e:
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)