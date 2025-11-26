from flask import Flask, request, jsonify
from flask_cors import CORS
import xml.etree.ElementTree as ET
from collections import defaultdict

app = Flask(__name__)
CORS(app)

# Namespaces BPMN 2.0
NS = {
    'bpmn': 'http://www.omg.org/spec/BPMN/20100524/MODEL',
    'bpmndi': 'http://www.omg.org/spec/BPMN/20100524/DI',
    'dc': 'http://www.omg.org/spec/DD/20100524/DC',
    'di': 'http://www.omg.org/spec/DD/20100524/DI'
}

def parse_bpmn_xml(xml_content):
    """Parse BPMN XML content and extract structured information"""
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as e:
        return {'error': f'XML parsing error: {str(e)}'}, 400
    
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
    
    # Find process element
    process = root.find('.//bpmn:process', NS)
    if process is None:
        return {'error': 'No BPMN process found in XML'}, 400
    
    # Extract process name and documentation
    result['title'] = process.get('name', 'Unnamed Process')
    doc = process.find('bpmn:documentation', NS)
    if doc is not None and doc.text:
        result['objective'] = doc.text.strip()
    
    # Extract all elements
    for elem_type in ['startEvent', 'endEvent', 'task', 'userTask', 'serviceTask', 
                      'manualTask', 'scriptTask', 'businessRuleTask', 'sendTask', 
                      'receiveTask', 'exclusiveGateway', 'parallelGateway', 
                      'inclusiveGateway', 'eventBasedGateway', 'complexGateway',
                      'subProcess', 'callActivity', 'intermediateCatchEvent', 
                      'intermediateThrowEvent', 'boundaryEvent']:
        for elem in process.findall(f'bpmn:{elem_type}', NS):
            elem_id = elem.get('id')
            elem_name = elem.get('name', '')
            elem_doc = elem.find('bpmn:documentation', NS)
            elem_doc_text = elem_doc.text.strip() if elem_doc is not None and elem_doc.text else ''
            
            result['elements'][elem_id] = {
                'type': elem_type,
                'name': elem_name,
                'documentation': elem_doc_text
            }
    
    # Extract sequence flows
    for flow in process.findall('bpmn:sequenceFlow', NS):
        flow_id = flow.get('id')
        result['flows'][flow_id] = {
            'name': flow.get('name', ''),
            'source': flow.get('sourceRef'),
            'target': flow.get('targetRef'),
            'condition': ''
        }
        
        # Extract condition expression
        condition = flow.find('bpmn:conditionExpression', NS)
        if condition is not None and condition.text:
            result['flows'][flow_id]['condition'] = condition.text.strip()
    
    # Extract lanes and their assignments
    for lane_set in process.findall('.//bpmn:laneSet', NS):
        for lane in lane_set.findall('bpmn:lane', NS):
            lane_name = lane.get('name', 'Unnamed Lane')
            for flow_node_ref in lane.findall('bpmn:flowNodeRef', NS):
                node_id = flow_node_ref.text
                result['lanes'][node_id] = lane_name
    
    # Extract data stores
    for data_store in root.findall('.//bpmn:dataStoreReference', NS):
        store_id = data_store.get('id')
        result['data_stores'][store_id] = {
            'name': data_store.get('name', ''),
            'type': 'dataStore'
        }
    
    # Extract text annotations
    for annotation in process.findall('bpmn:textAnnotation', NS):
        text_elem = annotation.find('bpmn:text', NS)
        if text_elem is not None and text_elem.text:
            annotation_id = annotation.get('id')
            
            # Find associated element
            associated_elem = None
            for assoc in process.findall('bpmn:association', NS):
                if assoc.get('sourceRef') == annotation_id:
                    associated_elem = assoc.get('targetRef')
                    break
                elif assoc.get('targetRef') == annotation_id:
                    associated_elem = assoc.get('sourceRef')
                    break
            
            result['annotations'].append({
                'id': annotation_id,
                'text': text_elem.text.strip(),
                'associated_element': associated_elem
            })
    
    # Build chronological flow order
    start_events = [eid for eid, elem in result['elements'].items() 
                    if elem['type'] == 'startEvent']
    
    if start_events:
        visited = set()
        flow_order = []
        
        def traverse_flow(elem_id, path=[]):
            if elem_id in visited or elem_id not in result['elements']:
                return
            
            visited.add(elem_id)
            elem = result['elements'][elem_id]
            
            flow_order.append({
                'id': elem_id,
                'name': elem['name'],
                'type': elem['type'],
                'actor': result['lanes'].get(elem_id, 'N/A'),
                'path': list(path)
            })
            
            # Find outgoing flows
            outgoing = [fid for fid, flow in result['flows'].items() 
                       if flow['source'] == elem_id]
            
            for flow_id in outgoing:
                target = result['flows'][flow_id]['target']
                new_path = path + [flow_id]
                traverse_flow(target, new_path)
        
        # Start traversal from each start event
        for start_id in start_events:
            traverse_flow(start_id)
        
        result['flow_order'] = flow_order
    
    # Add warnings for disconnected elements
    connected = set()
    for flow in result['flows'].values():
        connected.add(flow['source'])
        connected.add(flow['target'])
    
    disconnected = set(result['elements'].keys()) - connected
    if disconnected:
        result['warnings'].append({
            'type': 'disconnected_elements',
            'message': f'{len(disconnected)} element(s) are not connected to any flow',
            'elements': list(disconnected)
        })
    
    return result, 200

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'healthy'}), 200

@app.route('/parse-bpmn', methods=['POST'])
def parse_bpmn():
    """Endpoint to parse BPMN content sent as JSON"""
    try:
        # Check if JSON body exists
        if not request.is_json:
            return jsonify({'error': 'Content-Type must be application/json'}), 400
        
        data = request.get_json()
        
        # Check if 'content' field exists
        if 'content' not in data:
            return jsonify({'error': 'Missing "content" field in JSON body'}), 400
        
        xml_content = data['content']
        
        if not xml_content or not isinstance(xml_content, str):
            return jsonify({'error': 'Content must be a non-empty string'}), 400
        
        # Parse the BPMN XML content
        result, status_code = parse_bpmn_xml(xml_content)
        
        return jsonify(result), status_code
        
    except Exception as e:
        return jsonify({'error': f'Server error: {str(e)}'}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
