# rag_engine.py
import chromadb

def initialize_and_index_db(discovered_classes, collection_name="class_documentations"):
    """Initializes local ChromaDB and indexes class documentations."""
    chroma_client = chromadb.Client()
    
    # Reset/Create Collection
    try:
        chroma_client.delete_collection(name=collection_name)
    except Exception:
        pass
    collection = chroma_client.create_collection(name=collection_name)
    
    documents = []
    metadatas = []
    ids = []
    
    for i, cls in enumerate(discovered_classes):
        documents.append(cls['docstring'])
        metadatas.append({
            'class_name': cls['class_name'],
            'file_path': cls['file_path'],
            'methods': ','.join(cls['methods']),
        })
        ids.append(f"class_{i}")
        
    if documents:
        collection.add(
            documents=documents,
            metadatas=metadatas,
            ids=ids
        )
    return collection

def query_relevant_classes(collection, traceback_text, discovered_classes, n_results=3):
    """Queries ChromaDB using traceback context and maps results back to discovered classes."""
    query_results = collection.query(
        query_texts=[traceback_text],
        n_results=min(n_results, len(discovered_classes))
    )
    
    retrieved_classes = []
    if query_results and 'metadatas' in query_results and query_results['metadatas']:
        for metadata in query_results['metadatas'][0]:
            class_name = metadata['class_name']
            cls_obj = next((c for c in discovered_classes if c['class_name'] == class_name), None)
            if cls_obj:
                retrieved_classes.append(cls_obj)
    return retrieved_classes
