import hashlib
import io
import json
import os
import shutil
import importlib.util
import sys
import html
from visuals import flow_figure, coverage_figure, confidence_figure, decorate, probabilities
import plotly.express as px
import zipfile
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
import streamlit as st
from backend import COLUMNS, parse_items, run_harmonica

CATALOG = json.loads(Path(__file__).with_name('assets').joinpath('model_catalog.json').read_text())
if os.environ.get('HARMONICA_SOURCE_DIR'):
    sys.path.insert(0, os.environ['HARMONICA_SOURCE_DIR'])

st.set_page_config(page_title='HarmoniCA | Research workspace', page_icon='🎼', layout='wide')
st.markdown('''<style>
.hero {background:#102c43;padding:26px 32px;border-radius:16px;margin-bottom:20px;}
.hero h1 {color:#ffffff!important;margin:0;}
.hero p {color:#d9e8f4!important;margin:8px 0 0;}
</style>''', unsafe_allow_html=True)
st.markdown('<div class="hero"><h1>HarmoniCA · Research workspace</h1><p>Prepare items · map dimensions · compare instruments · document decisions</p></div>', unsafe_allow_html=True)
engine = importlib.util.find_spec('harmonica') is not None
with st.sidebar:
    st.subheader('Analysis settings')
    st.success('Engine available') if engine else st.warning('Engine not found')
    force = st.checkbox('Force model rerun')
    minutes = st.number_input('Run timeout (minutes)', 1, 180, 30)
    st.divider()
    threshold = st.slider('Flag confidence below', 0.0, 1.0, 0.65, 0.05)
    st.caption('A review threshold, not a validated error cutoff. Confidence is an engine score, not calibrated clinical certainty.')
    st.caption('First runs can download model weights. Keep the browser open until completion.')

items = None
source_name = 'example_items.csv'
prepare, explore, review, models = st.tabs(['Prepare & run', 'Visual dashboard', 'Item inspector', 'Models & dimensions'])
with prepare:
    left, right = st.columns([3,2])
    with left:
        st.subheader('Questionnaire input')
        mode = st.radio('Input source', ['Upload a file','Try example items','Explore reference inventory'], horizontal=True)
        upload = st.file_uploader('Upload CSV or Excel', type=['csv','xlsx'])
        sample = Path(__file__).with_name('example_items.csv').read_bytes()
        st.download_button('Download input template', sample, 'example_items.csv','text/csv')
        if mode == 'Explore reference inventory':
            seed = pd.read_csv(Path(__file__).with_name('assets')/'reference_inventory.csv',dtype=str,keep_default_na=False).drop_duplicates(['construct','questionnaire','item_id'],keep='last')
            reference_construct = st.selectbox('Reference construct',CATALOG['CONSTRUCTS'])
            choices = sorted(seed.loc[seed.construct==reference_construct,'questionnaire'].unique())
            reference_q = st.multiselect('Reference questionnaires',choices,default=choices[:2])
            raw = seed.loc[(seed.construct==reference_construct)&seed.questionnaire.isin(reference_q),COLUMNS].to_csv(index=False).encode()
        else:
            raw = sample if mode == 'Try example items' else (upload.getvalue() if upload else None)
        if raw is not None:
            source_name = upload.name if mode == 'Upload a file' else ('reference_items.csv' if mode == 'Explore reference inventory' else 'example_items.csv')
            try:
                if len(raw) > 10*1024*1024:
                    raise ValueError('File exceeds 10 MB.')
                if source_name.lower().endswith('.xlsx'):
                    workbook = pd.ExcelFile(io.BytesIO(raw))
                    sheet = st.selectbox('Worksheet', workbook.sheet_names)
                    frame = pd.read_excel(workbook, sheet_name=sheet, dtype=str).fillna('')
                else:
                    separator = st.selectbox('CSV separator', [',',';', '\t'], format_func=lambda x: {'，':'Comma',',':'Comma',';':'Semicolon','\t':'Tab'}[x])
                    frame = pd.read_csv(io.BytesIO(raw), sep=separator, dtype=str, keep_default_na=False, encoding='utf-8-sig')
                frame.columns = frame.columns.astype(str).str.strip()
                if frame.empty or not len(frame.columns):
                    raise ValueError('No item rows found.')
                st.caption(f'Loaded {len(frame)} rows. Match your columns to HarmoniCA fields.')
                mapping = {}
                cols = st.columns(2)
                for i, field in enumerate(COLUMNS):
                    options = ['— Select —'] + list(frame.columns)
                    with cols[i % 2]:
                        mapping[field] = st.selectbox(field, options, index=options.index(field) if field in options else 0, key=f'map_{field}_{hashlib.sha256(raw).hexdigest()[:10]}')
                chosen = list(mapping.values())
                if '— Select —' in chosen:
                    st.warning('Match all four columns to enable running.')
                elif len(set(chosen)) != 4:
                    st.error('Choose a different source column for each field.')
                else:
                    mapped = pd.DataFrame({field:frame[column] for field,column in mapping.items()})
                    with st.expander('Edit input items',expanded=False):
                        edited = st.data_editor(mapped, hide_index=True, num_rows='dynamic', width='stretch', key='input_'+hashlib.sha256(raw+str(mapping).encode()).hexdigest())
                    items = parse_items(edited.to_csv(index=False).encode())
                    unknown = set(items.construct)-set(CATALOG['CONSTRUCTS'])
                    if unknown:
                        raise ValueError('Unsupported constructs: '+', '.join(sorted(unknown))+'. Supported: '+', '.join(CATALOG['CONSTRUCTS']))
            except Exception as exc:
                st.error(f'Input needs attention: {exc}')
        else:
            st.info('Upload a file or choose “Try example items”. The Run panel will explain what is needed.')
    with right:
        st.subheader('Run mapping')
        if items is not None:
            constructs = st.multiselect('Constructs', sorted(items.construct.unique()), default=sorted(items.construct.unique()))
            questionnaires = st.multiselect('Questionnaires', sorted(items.questionnaire.unique()), default=sorted(items.questionnaire.unique()))
            selected = items[items.construct.isin(constructs) & items.questionnaire.isin(questionnaires)]
            st.metric('Items ready', len(selected))
        else:
            selected = pd.DataFrame(columns=COLUMNS)
            constructs = []
        fingerprint = hashlib.sha256(selected.to_csv(index=False).encode()+str(force).encode()).hexdigest()
        if st.session_state.get('fingerprint') != fingerprint:
            for key in ['result','result_bytes','log','manifest','decisions']:
                st.session_state.pop(key,None)
            st.session_state.fingerprint = fingerprint
        reason = 'Upload and validate your items.' if items is None else ('Select at least one item.' if selected.empty else ('Activate the environment where harmonica --help works.' if not engine else 'Ready to map selected items.'))
        st.info(reason)
        clicked = st.button('Run HarmoniCA', type='primary', disabled=items is None or selected.empty or not engine, width='stretch')
        st.caption('Only constructs supported by your installed HarmoniCA models can run. Input labels are not a list of available models.')
        if clicked:
            for key in ['result','result_bytes','log','manifest','decisions']:
                st.session_state.pop(key,None)
            try:
                with st.status('Running HarmoniCA…', expanded=True) as status:
                    st.write(f'Processing {len(selected)} items across {selected.questionnaire.nunique()} questionnaires.')
                    data, result, log = run_harmonica(selected, force, int(minutes)*60)
                    st.session_state.update(result=result, result_bytes=data, log=log, manifest={'created_utc':datetime.now(timezone.utc).isoformat(),'input_file':source_name,'input_sha256':hashlib.sha256(raw).hexdigest(),'submitted_sha256':hashlib.sha256(selected.to_csv(index=False).encode()).hexdigest(),'selected_items':len(selected),'constructs':constructs,'force_rerun':force,'output_rows':len(result),'upstream_commit':'b074bf07970959b4d5021e2cfbeff34a6b012385'})
                    status.update(label='Mapping completed. Open Compare or Review.',state='complete',expanded=False)
            except Exception as exc:
                st.error('Mapping failed. See the engine details below.')
                st.code(str(exc),language=None)

with explore:
    if 'result' not in st.session_state:
        st.info('Run mapping or explore the reference inventory in Prepare & run.')
    else:
        result=st.session_state.result
        st.subheader('Questionnaire → dimension landscape')
        a,b=st.columns(2)
        active_construct=a.selectbox('Explore construct',list(result.construct.unique()))
        subset=result[result.construct==active_construct].copy()
        qs=b.multiselect('Show questionnaires',list(subset.questionnaire.unique()),default=list(subset.questionnaire.unique()))
        subset=subset[subset.questionnaire.isin(qs)]
        subset['dimension_label']=subset.apply(lambda r: CATALOG['DIMENSION_DESCRIPTIONS'][active_construct].get(str(r.dimension).split('.')[0],{}).get('label',r.dimension_label),axis=1)
        dim_choices=list(subset.dimension_label.unique())
        dimension_filter=st.multiselect('Dimensions to display',dim_choices,default=dim_choices,key='dashboard_dimensions_'+active_construct)
        subset=subset[subset.dimension_label.isin(dimension_filter)]
        if subset.empty:
            st.info('Select a questionnaire to display its assignments.')
        else:
            numeric=pd.to_numeric(subset.confidence,errors='coerce')
            a,b,c,d=st.columns(4)
            a.metric('Mapped items',len(subset));b.metric('Questionnaires',subset.questionnaire.nunique())
            c.metric('Dimensions represented',subset.dimension_label.nunique());d.metric('Flagged for review',int((numeric<threshold).sum()))
            st.caption('Charts use catalog labels by dimension ID; original output wording is preserved in the inspector and export.')
            st.caption('Hover over flows to see item counts. Each view stays within one construct so dimensions remain comparable.')
            fig=flow_figure(subset)
            st.plotly_chart(fig,width='stretch',key='flow')
            a,b=st.columns([3,2])
            with a:
                st.subheader('Coverage map')
                percent=st.toggle('Show percentage within each questionnaire',value=True)
                st.plotly_chart(coverage_figure(subset,percent),width='stretch',key='coverage')
            with b:
                st.subheader('Assignment confidence')
                st.plotly_chart(confidence_figure(subset),width='stretch',key='confidence')
            st.caption('Coverage is item composition, not harmonized participant severity. Inventory confidence may reflect stored expert agreement rather than a fresh model score; full distributions are unavailable for those rows.')
            st.subheader('Items behind the view')
            st.caption('Filters above update these cards. Open Item inspector to record decisions.')
            for _,card in subset.head(6).iterrows():
                with st.container(border=True):
                    st.caption(f'{card.questionnaire} · {card.item_id}')
                    st.write(card.item_text)
                    st.write('**'+card.dimension_label+'**')
            if len(subset)>6:st.caption(f'Showing the first 6 of {len(subset)} filtered items; inspect every item in Item inspector.')
            with st.expander('Download interactive dashboard'):
                dashboard='<html><head><meta charset="utf-8"><title>HarmoniCA dashboard</title></head><body><h1>HarmoniCA item assignments</h1>'
                for chart in [fig,coverage_figure(subset,percent),confidence_figure(subset)]:
                    dashboard+=chart.to_html(full_html=False,include_plotlyjs=True)
                dashboard+='<p>Assignment counts and engine confidence. Not instrument equivalence or participant severity.</p></body></html>'
                st.download_button('Save interactive HTML',dashboard,'harmonica_dashboard.html','text/html')

with review:
    st.subheader('Inspect one item at a time')
    if 'result' not in st.session_state:
        st.info('Run mapping to unlock the item inspector.')
    else:
        result=st.session_state.result.reset_index(drop=True)
        if 'decisions' not in st.session_state:
            st.session_state.decisions={}
        a,b,c=st.columns([2,2,1])
        inspect_construct=a.selectbox('Construct',list(result.construct.unique()),key='inspect_construct')
        filtered=result[result.construct==inspect_construct]
        questionnaire=b.selectbox('Questionnaire',list(filtered.questionnaire.unique()),key='inspect_q')
        filtered=filtered[filtered.questionnaire==questionnaire]
        only_flagged=c.checkbox('Flagged only')
        if only_flagged:filtered=filtered[pd.to_numeric(filtered.confidence,errors='coerce')<threshold]
        search=st.text_input('Find item wording or ID')
        if search:filtered=filtered[filtered.item_text.str.contains(search,case=False,regex=False)|filtered.item_id.str.contains(search,case=False,regex=False)]
        if filtered.empty:
            st.info('No items match these filters.')
        else:
            index=st.selectbox('Choose item',list(filtered.index),format_func=lambda i:f'{result.loc[i,"item_id"]} · {result.loc[i,"item_text"][:90]}')
            row=result.loc[index]
            a,b=st.columns([3,2])
            with a:
                with st.container(border=True):
                    st.caption(f'{row.questionnaire} / {row.item_id}')
                    st.markdown('### '+row.item_text)
                    st.write('**Assigned dimension:** '+row.dimension_label)
                    conf=pd.to_numeric(row.confidence,errors='coerce')
                    st.metric('Engine confidence',f'{conf:.1%}' if pd.notna(conf) else 'Unavailable')
                    st.warning('Below your review threshold') if pd.notna(conf) and conf<threshold else st.caption('Review in the context of the construct definition.')
                    definition=CATALOG['DIMENSION_DESCRIPTIONS'].get(row.construct,{}).get(str(row.dimension).split('.')[0],{})
                    st.caption(definition.get('description','Definition not found in pinned catalog.'))
            with b:
                distribution=probabilities(row.get('probability_distribution',''))
                if distribution:
                    defs=CATALOG['DIMENSION_DESCRIPTIONS'][row.construct]
                    names=[defs.get(k,{}).get('label','Dimension '+k) for k in distribution]
                    prob_frame=pd.DataFrame({'Dimension':names,'Engine score':list(distribution.values())})
                    chart=decorate(px.bar(prob_frame,x='Engine score',y='Dimension',orientation='h',range_x=[0,1],color='Dimension'))
                    chart.update_layout(showlegend=False)
                    st.plotly_chart(chart,width='stretch',key='item_distribution')
                else:
                    st.info('This inventory assignment has no full probability distribution. No alternatives are fabricated.')
            previous=st.session_state.decisions.get(str(index),{})
            with st.form('decision_'+str(index)):
                a,b=st.columns(2)
                states=['Pending','Accepted','Changed','Uncertain']
                status=a.selectbox('Decision',states,index=states.index(previous.get('review_status','Pending')))
                options=list(dict.fromkeys([row.dimension_label]+[v['label'] for v in CATALOG['DIMENSION_DESCRIPTIONS'][row.construct].values()]))
                current=previous.get('reviewed_dimension',row.dimension_label)
                revised=b.selectbox('Reviewed dimension',options,index=options.index(current) if current in options else 0)
                note=st.text_area('Reason / note',previous.get('review_note',''))
                if st.form_submit_button('Save review decision',type='primary'):
                    if status=='Changed' and revised==row.dimension_label:
                        st.error('Choose a different dimension for a Changed decision.')
                    else:
                        st.session_state.decisions[str(index)]={'review_status':status,'reviewed_dimension':revised,'review_note':note}
                        st.success('Decision saved in this session.')
        st.divider()
        reviewed=result.copy()
        reviewed['review_status']='Pending';reviewed['reviewed_dimension']='';reviewed['review_note']=''
        for key,decision in st.session_state.decisions.items():
            for field,value in decision.items():reviewed.loc[int(key),field]=value
        count=int((reviewed.review_status!='Pending').sum())
        st.progress(count/len(reviewed),text=f'{count} / {len(reviewed)} items reviewed')
        record=dict(st.session_state.manifest);record['reviewed_rows']=count
        archive=io.BytesIO()
        with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
            z.writestr('harmonized_items.csv',st.session_state.result_bytes)
            z.writestr('researcher_review.csv',reviewed.to_csv(index=False))
            final=reviewed.copy()
            final['effective_dimension_label']=final.apply(lambda r:r.reviewed_dimension if r.review_status=='Changed' else r.dimension_label,axis=1)
            final['effective_dimension']=final.dimension
            for i,r in final.iterrows():
                if r.review_status=='Changed':
                    definitions=CATALOG['DIMENSION_DESCRIPTIONS'][r.construct]
                    final.loc[i,'effective_dimension']=next((key for key,val in definitions.items() if val['label']==r.reviewed_dimension),r.dimension)
            z.writestr('reviewed_harmonized_items.csv',final.to_csv(index=False))
            z.writestr('OUTPUT_README.txt','harmonized_items.csv: unchanged HarmoniCA item-to-dimension assignments. reviewed_harmonized_items.csv: effective_dimension and effective_dimension_label incorporate explicitly Changed review decisions; review_status distinguishes pending/uncertain rows. researcher_review.csv: audit trail. These files contain item mappings, not harmonized participant scores. Join later using construct, questionnaire and item_id. Do not assume item_id is globally unique.')
            z.writestr('submitted_items.csv',selected.to_csv(index=False))
            z.writestr('run_record.json',json.dumps(record,indent=2))
            z.writestr('engine.log',st.session_state.log)
        a,b=st.columns(2)
        a.download_button('Download harmonized items CSV',st.session_state.result_bytes,'harmonized_items.csv','text/csv',type='primary')
        b.download_button('Download complete review bundle',archive.getvalue(),'harmonica_analysis.zip','application/zip',type='primary')
        with st.expander('Raw output / engine log'):
            st.dataframe(reviewed,hide_index=True,width='stretch')
            st.code(st.session_state.log or 'No messages.',language=None)

with models:
    st.subheader('Models and dimension definitions')
    st.caption('Configuration inspected from julia-pfarr/HarmoniCA at commit b074bf0. This catalog does not prove model weights have downloaded successfully.')
    model_construct=st.selectbox('Browse a construct',CATALOG['CONSTRUCTS'],key='catalog_construct')
    a,b,c=st.columns(3)
    a.metric('Dimensions',len(CATALOG['DIMENSION_DESCRIPTIONS'][model_construct]))
    b.metric('Model strategy',{'ft':'Fine-tuned','ft_knn':'Fine-tuned + kNN','base_knn':'Base encoder + kNN'}[CATALOG['BEST_MODEL'][model_construct]])
    c.metric('Reference items',int((pd.read_csv(Path(__file__).with_name('assets')/'reference_inventory.csv').construct==model_construct).sum()))
    st.link_button('View upstream model repository','https://huggingface.co/'+CATALOG['HF_REPOS'][model_construct])
    if model_construct in CATALOG.get('BASE_MODEL_NAMES',{}):
        st.link_button('View base encoder','https://huggingface.co/'+CATALOG['BASE_MODEL_NAMES'][model_construct])
    for dimension,definition in CATALOG['DIMENSION_DESCRIPTIONS'][model_construct].items():
        with st.container(border=True):
            st.markdown('**'+dimension+' · '+definition['label']+'**')
            st.write(definition['description'])
