import { Component, Element, h, Prop, State, Watch } from '@stencil/core';
import * as vis from 'vis-network';
import * as visData from 'vis-data';

type EndpointBody = {
  gph?: string | null;
  nd?: string | null;
  mode?: 'default';
  detailed?: boolean;
  show_edges?: boolean;
};

type Graph = {
  name: string;
  kind: string;
  jid: string;
  j_timestamp: string;
  j_type: 'graph';
  context: Record<any, any>;
};

@Component({
  tag: 'jsc-graph',
  styleUrl: 'jsc-graph.css',
  shadow: true,
})
export class JscGraph {
  @Element() host: HTMLElement;
  @Prop() css: string = JSON.stringify({});
  @Prop({ mutable: true }) events: string;
  @Prop() token: string = '';
  @Prop() graphId: string = '';
  @Prop({ attribute: 'serverurl' }) serverUrl: string = 'http://localhost:8888';
  @Prop() onFocus: 'expand' | 'isolate' = 'expand';
  @Prop() height = '100vh';

  // viewed node
  @State() nd = '';
  @State() prevNd = '';
  @State() network: vis.Network;
  @State() graphs: Graph[] = [];

  nodesArray: vis.Node[] = [];
  edgesArray: vis.Edge[] = [];
  edges: visData.DataSet<any, string>;
  nodes: vis.data.DataSet<any, string>;

  @State() clickedNode: vis.Node & { context: {} };
  @State() clickedEdge: vis.Edge & { context: {} };

  networkEl!: HTMLDivElement;

  async getActiveGraph(): Promise<Graph> {
    return await fetch(`${this.serverUrl}/js/graph_active_get`, {
      method: 'post',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `token ${localStorage.getItem('token')}`,
      },
    }).then(res => res.json());
  }

  async getAllGraphs(): Promise<Graph[]> {
    return await fetch(`${this.serverUrl}/js/graph_list`, {
      method: 'post',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `token ${localStorage.getItem('token')}`,
      },
    }).then(res => res.json());
  }

  @Watch('nd')
  async getGraphState() {
    let body: EndpointBody = { detailed: true, gph: this.graphId, mode: 'default' };
    let endpoint = `${this.serverUrl}/js/graph_get`;

    if (this.nd) {
      endpoint = `${this.serverUrl}/js/graph_node_view`;

      body = {
        nd: this.nd,
        show_edges: true,
        detailed: true,
      };
    }

    return fetch(endpoint, {
      method: 'post',
      body: JSON.stringify(body),
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `token ${localStorage.getItem('token')}`,
      },
    }).then(async res => {
      const data = await res.json();
      if (this.nd && this.onFocus === 'expand' && this.prevNd !== '') {
        // create datasets
        if (!this.edges && !this.nodes) {
          this.nodes = new visData.DataSet([]);
          this.edges = new visData.DataSet([]);
        }

        if (this.network) {
          this.network.stabilize();
          this.network.storePositions();
        }

        // expand nodes and edges sets
        this.formatNodes(data).forEach(node => {
          try {
            if (!this.nodes.get(node.id)) {
              this.nodes.add(node);
            }
          } catch (err) {
            console.log(err);
          }
        });

        this.formatEdges(data).forEach(edge => {
          if (!this.edges.get({ filter: item => item.to === edge.to }).length) {
            this.edges.add(edge);
          }
        });
      } else {
        this.nodes = new visData.DataSet(this.formatNodes(data) as any);
        this.edges = new visData.DataSet(this.formatEdges(data) as any);

        // update view when viewing the full graph
        if (this.network) {
          this.network.setData({ edges: this.edges as any, nodes: this.nodes as any });
        }
      }

      if (!this.network) {
        this.network = new vis.Network(
          this.networkEl,
          { edges: this.edges as any, nodes: this.nodes as any },
          {
            // width: '100%',
            edges: {
              arrows: { to: { enabled: true, scaleFactor: 0.5 } },
              dashes: true,
              selectionWidth: 2,
              width: 0.5,
              color: {},
            },
            interaction: {},
            // layout: { improvedLayout: true },
          },
        );
      } else {
        this.network.stabilize();
        this.network.storePositions();

        // this.network.setData({ edges: this.edgesArray, nodes: this.nodesArray });
        this.network.selectNodes([this.nd], true);
        this.network.focus(this.nd, { scale: 2 });
      }
    });
  }

  @Watch('graphId')
  async refreshGraph() {
    await this.getGraphState();
  }

  // convert response to match required format for vis
  @Watch('nd')
  formatNodes(data: [][]): vis.Node[] {
    return data
      ?.filter((item: any) => item.j_type === 'node')
      .map((node: any) => ({
        id: node.jid,
        label: node.name,
        group: node.name,
        context: node.context,
        shape: 'circle',
      }));
  }

  handleNetworkClick(network: vis.Network, params?: any) {
    const selection = network.getSelection();
    const node = network.getNodeAt({
      x: params?.pointer.DOM.x,
      y: params?.pointer.DOM.y,
    });

    const edge = network.getEdgeAt({
      x: params?.pointer.DOM.x,
      y: params?.pointer.DOM.y,
    });

    // we don't want to have a clicked edge if we click on a node
    if (selection.nodes.length) {
      this.clickedNode = this.nodes.get([node])[0];
      this.clickedEdge = undefined;
    } else {
      this.clickedEdge = this.edges.get([edge])[0];
    }

    console.log({ node, edge });
    console.log({ node: this.clickedNode, edge: this.clickedEdge });
  }

  // convert response to match required format for vis
  formatEdges(data: {}[]): vis.Edge[] {
    return data
      ?.filter((item: any) => item.j_type === 'edge')
      .map((edge: any) => ({
        from: edge.from_node_id,
        to: edge.to_node_id,
        label: edge.name,
        context: edge.context,
        group: edge.name,
      }));
  }

  renderContext() {
    let context: undefined | Record<any, any> = {};
    if (this.clickedEdge?.context) {
      context = this.clickedEdge.context;
    } else {
      context = this.clickedNode?.context;
    }

    return context ? (
      Object.keys(context).map(contextKey => (
        <div key={contextKey}>
          <p style={{ fontWeight: 'bold' }}>{contextKey}</p>
          <p>
            {Array.isArray(context[contextKey])
              ? context[contextKey].map(item => item.toString()).join(', ')
              : typeof context[contextKey] === 'boolean'
              ? context[contextKey]?.toString()
              : context[contextKey]}
          </p>
        </div>
      ))
    ) : (
      <p>Select a node or edge with contextual data</p>
    );
  }

  async componentDidLoad() {
    try {
      // set the initial graph
      let activeGraph: Graph = await this.getActiveGraph();
      this.graphId = activeGraph?.jid;

      // get all graphs for the graph switcher
      this.graphs = await this.getAllGraphs();

      await this.getGraphState();
    } catch (err) {
      console.log(err);
    }

    this.network.on('click', params => {
      this.handleNetworkClick(this.network, params);
    });

    this.network.on('doubleClick', async params => {
      this.prevNd = this.nd;

      const node = this.network.getNodeAt({
        x: params?.pointer.DOM.x,
        y: params?.pointer.DOM.y,
      });

      this.nd = node.toString();

      console.log({ nd: this.nd });
    });

    this.network.on('oncontext', params => {
      params.event.preventDefault();
      const node = this.network.getNodeAt({
        x: params.pointer.DOM.x,
        y: params.pointer.DOM.y,
      });

      if (node) {
        // this.nd = node.toString();
        // select and focus on node
        this.network.selectNodes([node]);
        this.network.focus(node, {
          scale: 1.0,
          animation: { duration: 1000, easingFunction: 'easeInOutQuad' },
        });
      }
    });
  }

  render() {
    return (
      <div data-theme={'greenheart'}>
        {!localStorage.getItem('token') ? (
          <div style={{ width: '520px', margin: '40px auto' }}>
            <jsc-card title={'Login'}>
              <jsc-auth-form slot={'children'} serverURL={this.serverUrl} redirectURL={window.location.toString()}></jsc-auth-form>
            </jsc-card>
          </div>
        ) : (
          <div style={{ height: this.height, width: 'auto', position: 'relative' }}>
            <div
              style={{
                height: '260px',
                width: '240px',
                borderRadius: '4px',
                padding: '16px',
                top: '20px',
                right: '20px',
                position: 'absolute',
                zIndex: '9999',
                border: '2px solid #f4f4f4',
                background: '#fff',
                boxShadow: 'rgb(0 0 0 / 15%) 0px 1px 2px 0px, rgb(0 0 0 / 2%) 0px 0px 2px 1px',
                overflowY: 'auto',
                overflowX: 'hidden',
              }}
            >
              <div tabindex="0" class="collapse collapse-plus border border-base-300 bg-base-100 rounded-box">
                <input type="checkbox" defaultChecked={true} />
                <div class="collapse-title text-md font-medium">Context</div>
                <div class="collapse-content">{this.renderContext()}</div>
              </div>

              <div tabindex={0} class={'collapse collapse-plus border border-base-300 bg-base-100 rounded-box mt-2'}>
                <input type={'checkbox'} defaultChecked={true} />
                <div class={'collapse-title text-md font-medium'}>Behaviour</div>
                <div class="collapse-content">
                  <jsc-checkbox
                    label={'Expand nodes on click'}
                    size={'sm'}
                    value={String(this.onFocus === 'expand')}
                    onValueChanged={event => {
                      event.detail === 'true' ? (this.onFocus = 'expand') : (this.onFocus = 'isolate');
                    }}
                  ></jsc-checkbox>
                </div>
              </div>
            </div>
            <div style={{ position: 'absolute', top: '20px', left: '20px', zIndex: '9999' }}>
              {this.nd && <jsc-button size="sm" label={'View Full Graph'} onClick={() => (this.nd = '')}></jsc-button>}
            </div>

            {/*Graph Switcher*/}
            <div style={{ position: 'absolute', bottom: '20px', left: '20px', zIndex: '9999' }}>
              <jsc-select
                placeholder={'Select Graph'}
                onValueChanged={e => {
                  this.graphId = e.detail.split(':').slice(1).join(':');
                  localStorage.setItem('selectedGraph', this.graphId);
                }}
                options={this.graphs?.map(graph => ({ label: `${graph.name}:${graph.jid}` }))}
              ></jsc-select>
            </div>
            <div ref={el => (this.networkEl = el)} id={'network'} style={{ height: this.height }}></div>
          </div>
        )}
      </div>
    );
  }
}