import numpy as np
from tools import list_powerset


# creates a list of possible graphs by trying to link the last data point indicated by ind to the graph of previous ones
# result[0] contains the correct graph
#   len(result) = (ind + 2) * r_c, r_c: count of roots (before ind)
def forest_candidates(parents, ind):
    # assert ind < len(parents), 'ind = ' + str(ind) + ' exceeds data length = ' + str(len(parents))

    parent_correct = parents[ind]
    parents[ind] = -(ind + 1)   # point to outside, should not go into roots
    roots_orig = get_roots(parents)
    # cut all edges pointing to ind
    roots_cutout = cutout_leaf(parents, ind)
    children = get_children(parents)

    current_roots = roots_orig + roots_cutout

    correct_forrest_ind = -1

    forests = []
    i = 0

    # walk over the target roots (includes ind!)
    for root_idx, root_target in enumerate(current_roots + [ind]):
        # all roots before the target root can point to ind (in all combinations)
        for roots_subset in list_powerset(current_roots[:root_idx]):
            # add the roots right of the target. They have to point to ind to get projectivity
            children_candidates = roots_subset + current_roots[root_idx+1:]
            # walk over accessible nodes (projective constraint) in the tree below target_root
            for parent_target_candidate in outer_nodes(children, root_target):
                # save the correct id: exactly the cut out roots have to be the children of ind
                if np.array_equal(children_candidates, roots_cutout) and parent_target_candidate - ind == parent_correct:
                    correct_forrest_ind = i
                forests.append((children_candidates, parent_target_candidate - ind))
                i += 1

    return forests, correct_forrest_ind, roots_orig, roots_cutout


# creates a subgraph of the forest represented by parents
# parents outside the new graph are linked to itself
def cut_subgraph(parents):
    # assert start < len(parents), 'start_ind = ' + str(start) + ' exceeds list size = ' + str(len(parents))
    new_roots = []
    for i in range(len(parents)):
        if parents[i] < -i or parents[i] >= len(parents) - i:
            new_roots.append((i, parents[i]))
            parents[i] = 0
    return new_roots


def get_roots(parents):
    return [i[0] for i, parent in np.ndenumerate(parents) if parent == 0]


def get_children(parents):
    result = {}
    for i, parent in np.ndenumerate(parents):
        if parent == 0:
            continue
        i = i[0]
        parent_pos = i + parent
        if parent_pos not in result:
            result[parent_pos] = [i]
        else:
            result[parent_pos] += [i]
    return result


# modifies parents
# leaf parent points outside
def cutout_leaf(parents, pos):
    # assert pos < len(parents), 'pos = ' + str(pos) + ' exceeds list size = ' + str(len(parents))
    new_roots = []
    for i, parent in np.ndenumerate(parents):
        i = i[0]
        if i+parent == pos and parent != 0:
            parents[i] = 0
            new_roots.append(i)

    return new_roots


def left_outer_nodes(children, root):
    if root not in children or children[root][0] > root:
        return [root]
    return left_outer_nodes(children, children[root][0]) + [root]


def right_outer_nodes(children, root):
    if root not in children or children[root][-1] < root:
        return [root]
    return [root] + right_outer_nodes(children, children[root][-1])


def outer_nodes(children, root):
    return left_outer_nodes(children, root)[:-1] + right_outer_nodes(children, root)


# TODO: move this into Net
def calc_embedding(net, data, types, parents, edges):
    roots = np.where(parents == 0)[0]
    assert len(roots) == 1, 'more then one root'
    children = get_children(parents)
    embeddings = [None] * len(data)
    net.calc_embedding_single(data, types, children, edges, roots[0], embeddings)
    return embeddings[roots[0]].squeeze().data.numpy()
