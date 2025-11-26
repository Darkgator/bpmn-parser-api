from flask import Flask, request, jsonify
from flask_cors import CORS
import xml.etree.ElementTree as ET
from collections import defaultdict

app = Flask(__name__)
CORS(app)

# Namespaces BPMN 2.0
NS = {
    'bpmn': 'http://www.omg.org/spec/BPMN/20100524/MODEL',
    'bpmn2': 'http://www.omg.org/spec/BPMN/20100524/MODEL',
    'bpmndi': 'http://www.omg.org/spec/BPMN/20100524/DI',
    'dc': 'http://www.omg.org/spec/DD/20100524/DC',
    'di': 'http://www.omg.org/spec/DD/20100524/DI',
    'camunda': 'http://camunda.org/schema/1.0/bpmn',
    'xsi': 'http://www.w3.org/2001/XMLSchema-instance'
}

def safe_find_text(element, path, namespaces):
    """Safely find text in XML element"""
    found = element.find(path, namespaces)
    return found.text.strip() if found is not None and found.text else ''

def extract_documentation(element):
    """Extract documentation from element"""
    doc = element.find('bpmn:documentation', NS)
    if doc is None:
        doc = element.find('bpmn2:documentation', NS)
    return doc.text.strip() if doc is not None and doc.text else ''

def parse_bpmn_xml(xml_content):
    """Parse BPMN XML content and extract ALL structured information"""
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
        'pools': {},
        'data_stores': {},
        'data_objects': {},
        'data_associations': [],
        'annotations': [],
        'flow_order': [],
        'warnings': [],
        'processes': []
    }
    
    # Extract collaboration (pools and lanes)
    collaboration = root.find('.//bpmn:collaboration', NS) or root.find('.//bpmn2:collaboration', NS)
    if collaboration:
        for participant in collaboration.findall('bpmn:participant', NS) + collaboration.findall('bpmn2:participant', NS):
            part_id = participant.get('id')
            part_name = participant.get('name', 'Unnamed Pool')
            process_ref = participant.get('processRef')
            result['pools'][part_id] = {
                'name': part_name,
                'processRef': process_ref
            }
    
    # Find ALL processes
    all_processes = root.findall('.//bpmn:process', NS) + root.findall('.//bpmn2:process', NS)
    
    if not all_processes:
        return {'error': 'No BPMN process found in XML'}, 400
    
    # FILTER: Only keep processes that have actual content (not just Bizagi headers)
    element_types = [
        'startEvent', 'endEvent', 'intermediateThrowEvent', 'intermediateCatchEvent',
        'boundaryEvent', 'task', 'userTask', 'serviceTask', 'manualTask', 
        'scriptTask', 'businessRuleTask', 'sendTask', 'receiveTask',
        'exclusiveGateway', 'parallelGateway', 'inclusiveGateway', 
        'eventBasedGateway', 'complexGateway', 'subProcess', 'callActivity'
    ]
    
    # Filter valid processes (those with at least 1 element OR isExecutable="true")
    valid_processes = []
    for proc in all_processes:
        is_executable = proc.get('isExecutable', 'false').lower() == 'true'
        has_elements = False
        
        # Check if process has any real elements
        for elem_type in element_types:
            if proc.find(f'.//bpmn:{elem_type}', NS) is not None or proc.find(f'.//bpmn2:{elem_type}', NS) is not None:
                has_elements = True
                break
        
        # Keep process if it's executable OR has elements
        if is_executable or has_elements:
            valid_processes.append(proc)
    
    if not valid_processes:
        return {'error': 'No valid BPMN process with content found in XML'}, 400
    
    processes = valid_processes
    
    # Use first VALID process title/objective as main
    first_process = processes[0]
    result['title'] = first_process.get('name', 'Unnamed Process')
    result['objective'] = extract_documentation(first_process)
    
    # Process each VALID process
    for proc_idx, process in enumerate(processes):
        process_id = process.get('id')
        process_name = process.get('name', f'Process {proc_idx + 1}')
        
        result['processes'].append({
            'id': process_id,
            'name': process_name,
            'documentation': extract_documentation(process)
        })
        
        # Extract lanes from this process
        for lane_set in process.findall('.//bpmn:laneSet', NS) + process.findall('.//bpmn2:laneSet', NS):
            for lane in lane_set.findall('bpmn:lane', NS) + lane_set.findall('bpmn2:lane', NS):
                lane_id = lane.get('id')
                lane_name = lane.get('name', 'Unnamed Lane')
                
                # Map all flowNodeRefs to this lane
                for flow_node_ref in lane.findall('bpmn:flowNodeRef', NS) + lane.findall('bpmn2:flowNodeRef', NS):
                    node_id = flow_node_ref.text
                    if node_id:
                        result['lanes'][node_id] = lane_name
        
        # Extract all elements from this process
        for elem_type in element_types:
            for elem in process.findall(f'.//bpmn:{elem_type}', NS) + process.findall(f'.//bpmn2:{elem_type}', NS):
                elem_id = elem.get('id')
                if not elem_id:
                    continue
                
                elem_name = elem.get('name', '')
                elem_doc = extract_documentation(elem)
                
                result['elements'][elem_id] = {
                    'type': elem_type,
                    'name': elem_name,
                    'documentation': elem_doc,
                    'process': process_id
                }
                
                # Check for event definitions (for intermediate/boundary events)
                event_defs = []
                for event_def_type in ['messageEventDefinition', 'timerEventDefinition', 
                                      'errorEventDefinition', 'signalEventDefinition',
                                      'conditionalEventDefinition', 'linkEventDefinition']:
                    if elem.find(f'bpmn:{event_def_type}', NS) is not None or elem.find(f'bpmn2:{event_def_type}', NS) is not None:
                        event_defs.append(event_def_type.replace('EventDefinition', ''))
                
                if event_defs:
                    result['elements'][elem_id]['eventDefinitions'] = event_defs
        
        # Extract sequence flows from this process
        for flow in process.findall('.//bpmn:sequenceFlow', NS) + process.findall('.//bpmn2:sequenceFlow', NS):
            flow_id = flow.get('id')
            if not flow_id:
                continue
            
            result['flows'][flow_id] = {
                'name': flow.get('name', ''),
                'source': flow.get('sourceRef'),
                'target': flow.get('targetRef'),
                'condition': '',
                'process': process_id
            }
            
            # Extract condition expression
            condition = flow.find('bpmn:conditionExpression', NS) or flow.find('bpmn2:conditionExpression', NS)
            if condition is not None and condition.text:
                result['flows'][flow_id]['condition'] = condition.text.strip()
        
        # Extract data stores from this process
        for data_store in process.findall('.//bpmn:dataStoreReference', NS) + process.findall('.//bpmn2:dataStoreReference', NS):
            store_id = data_store.get('id')
            if store_id:
                result['data_stores'][store_id] = {
                    'name': data_store.get('name', 'Unnamed Data Store'),
                    'type': 'dataStoreReference',
                    'dataStoreRef': data_store.get('dataStoreRef', '')
                }
        
        # Extract data objects from this process
        for data_obj in process.findall('.//bpmn:dataObjectReference', NS) + process.findall('.//bpmn2:dataObjectReference', NS):
            obj_id = data_obj.get('id')
            if obj_id:
                result['data_objects'][obj_id] = {
                    'name': data_obj.get('name', 'Unnamed Data Object'),
                    'type': 'dataObjectReference',
                    'dataObjectRef': data_obj.get('dataObjectRef', '')
                }
        
        # Extract data input/output associations
        for task_elem in process.findall('.//*[@id]', NS):
            task_id = task_elem.get('id')
            
            # Data input associations
            for data_input_assoc in task_elem.findall('.//bpmn:dataInputAssociation', NS) + task_elem.findall('.//bpmn2:dataInputAssociation', NS):
                source_ref = safe_find_text(data_input_assoc, 'bpmn:sourceRef', NS) or safe_find_text(data_input_assoc, 'bpmn2:sourceRef', NS)
                target_ref = safe_find_text(data_input_assoc, 'bpmn:targetRef', NS) or safe_find_text(data_input_assoc, 'bpmn2:targetRef', NS)
                
                if source_ref:
                    result['data_associations'].append({
                        'type': 'input',
                        'from': source_ref,
                        'to': task_id,
                        'target': target_ref
                    })
            
            # Data output associations
            for data_output_assoc in task_elem.findall('.//bpmn:dataOutputAssociation', NS) + task_elem.findall('.//bpmn2:dataOutputAssociation', NS):
                source_ref = safe_find_text(data_output_assoc, 'bpmn:sourceRef', NS) or safe_find_text(data_output_assoc, 'bpmn2:sourceRef', NS)
                target_ref = safe_find_text(data_output_assoc, 'bpmn:targetRef', NS) or safe_find_text(data_output_assoc, 'bpmn2:targetRef', NS)
                
                if target_ref:
                    result['data_associations'].append({
                        'type': 'output',
                        'from': task_id,
                        'to': target_ref,
                        'source': source_ref
                    })
        
        # Extract text annotations
        for annotation in process.findall('.//bpmn:textAnnotation', NS) + process.findall('.//bpmn2:textAnnotation', NS):
            text_elem = annotation.find('bpmn:text', NS) or annotation.find('bpmn2:text', NS)
            if text_elem is not None and text_elem.text:
                annotation_id = annotation.get('id')
                
                # Find associated element via association
                associated_elem = None
                for assoc in process.findall('.//bpmn:association', NS) + process.findall('.//bpmn2:association', NS):
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
    
    # Build chronological flow order (from all VALID processes)
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
            
            flow_entry = {
                'id': elem_id,
                'name': elem['name'],
                'type': elem['type'],
                'actor': result['lanes'].get(elem_id, 'N/A'),
                'path': list(path),
                'documentation': elem.get('documentation', '')
            }
            
            # Add event definitions if present
            if 'eventDefinitions' in elem:
                flow_entry['eventDefinitions'] = elem['eventDefinitions']
            
            flow_order.append(flow_entry)
            
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
    
    # Check for missing lane assignments
    unassigned = set(result['elements'].keys()) - set(result['lanes'].keys())
    if unassigned:
        result['warnings'].append({
            'type': 'unassigned_lanes',
            'message': f'{len(unassigned)} element(s) are not assigned to any lane/pool',
            'elements': list(unassigned)
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
