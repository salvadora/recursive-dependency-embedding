from __future__ import print_function
import tensorflow as tf
import tensorflow_fold as td
import model_fold
import preprocessing
import spacy
import pickle
import pprint
import os
import sequence_node_sequence_pb2
import sequence_node_candidates_pb2
import numpy as np
import random

# Replication flags:
tf.flags.DEFINE_string('logdir', '/home/arne/ML_local/tf/log', #'/home/arne/tmp/tf/log',
                       'Directory in which to write event logs and model checkpoints.')
tf.flags.DEFINE_string('train_data_path', '/media/arne/WIN/Users/Arne/ML/data/corpora/wikipedia/process_sentence2/WIKIPEDIA_articles1000_maxdepth10',#'/home/arne/tmp/tf/log/model.ckpt-976',
                       'train data base path (without extension)')
#tf.flags.DEFINE_string('data_mapping_path', 'data/nlp/spacy/dict.mapping',
#                       'model file')
tf.flags.DEFINE_string('train_dict_file', 'data/nlp/spacy/dict.vecs',
                       'A file containing a numpy array which is used to initialize the embedding vectors.')
tf.flags.DEFINE_integer('pad_embeddings_to_size', 1300000,
                        'The initial GloVe embedding matrix loaded from spaCy is padded to hold unknown lexical ids '
                        '(dependency edge types, pos tag types, or any other type added by the sentence_processor to '
                        'mark identity). This value has to be larger then the initial gloVe size ()')
tf.flags.DEFINE_integer('max_depth', 10,
                        'The maximal depth of the sequence trees.')
tf.flags.DEFINE_integer('sample_count', 15,
                        'The amount of generated samples per correct sequence tree.')
tf.flags.DEFINE_integer('batch_size', 100,#250,
                        'How many samples to read per batch.')
tf.flags.DEFINE_integer('max_steps', 10,
                        'The maximum number of batches to run the trainer for.')
tf.flags.DEFINE_string('master', '',
                       'Tensorflow master to use.')
tf.flags.DEFINE_integer('task', 0,
                        'Task ID of the replica running the training.')
tf.flags.DEFINE_integer('ps_tasks', 0,
                        'Number of PS tasks in the job.')
FLAGS = tf.flags.FLAGS

PROTO_PACKAGE_NAME = 'recursive_dependency_embedding'
PROTO_CLASS = 'SequenceNodeSequence'
PROTO_FILE_NAME = 'sequence_node_sequence.proto'


# DEPRECATED
def parse_iterator(sequences, parser, sentence_processor, data_maps):
    #pp = pprint.PrettyPrinter(indent=2)
    for (s, idx_correct) in sequences:
        seq_tree_seq = sequence_node_sequence_pb2.SequenceNodeSequence()
        seq_tree_seq.idx_correct = idx_correct
        for s2 in s:
            new_tree = seq_tree_seq.trees.add()
            preprocessing.build_sequence_tree_from_str(s2, sentence_processor, parser, data_maps, seq_tree=new_tree)
        #pp.pprint(seq_tree_seq)
        yield td.proto_tools.serialized_message_to_tree('recursive_dependency_embedding.SequenceNodeSequence', seq_tree_seq.SerializeToString())


# DEPRECATED
def parse_iterator_candidates(sequences, parser, sentence_processor, data_maps):
    pp = pprint.PrettyPrinter(indent=2)
    for s in sequences:
        seq_data, seq_parents = preprocessing.read_data(preprocessing.identity_reader, sentence_processor, parser, data_maps,
                                          args={'content': s}, expand_dict=False)
        children, roots = preprocessing.children_and_roots(seq_parents)

        # dummy position
        insert_idx = 5
        candidate_indices = [2, 8]
        max_depth = 6
        max_dandidate_depth = 1
        seq_tree_seq = sequence_node_sequence_pb2.SequenceNodeSequence()
        seq_tree_seq.idx_correct = 0
        for candidate_idx in candidate_indices:
            preprocessing.build_sequence_tree_with_candidate(seq_data, children, roots[0], insert_idx, max_depth, max_dandidate_depth, candidate_idx, seq_tree=seq_tree_seq.trees.add())
        pp.pprint(seq_tree_seq)
        yield td.proto_tools.serialized_message_to_tree('recursive_dependency_embedding.SequenceNodeSequence', seq_tree_seq.SerializeToString())


def iterator_sequence_trees(corpus_path, max_depth, seq_data, children, sample_count):
    pp = pprint.PrettyPrinter(indent=2)

    # load corpus depth_max dependent data:
    print('create collected shuffled children indices ...')
    children_indices = preprocessing.collected_shuffled_child_indices(corpus_path, max_depth)
    #print(children_indices.shape)
    print('size: ' + str(len(children_indices)))
    print('load depths from: '+corpus_path + '.depth'+str(max_depth-1)+'.collected')
    depths_collected = np.load(corpus_path + '.depth'+str(max_depth-1)+'.collected')
    print('current depth size: '+str(len(depths_collected)))
    for child_tuple in children_indices:
        idx = child_tuple[0]
        idx_child = child_tuple[1]
        path_len = child_tuple[2]

        max_candidate_depth = max_depth - path_len
        seq_tree_seq = sequence_node_sequence_pb2.SequenceNodeSequence()
        seq_tree_seq.idx_correct = 0
        # add correct tree
        preprocessing.build_sequence_tree_with_candidate(seq_data, children, idx, idx_child, max_depth,
                                                         max_candidate_depth, idx_child,
                                                         seq_tree=seq_tree_seq.trees.add())
        # add samples
        for _ in range(sample_count):
            candidate_idx = np.random.choice(depths_collected)
            preprocessing.build_sequence_tree_with_candidate(seq_data, children, idx, idx_child, max_depth,
                                                             max_candidate_depth, candidate_idx,
                                                             seq_tree=seq_tree_seq.trees.add())
        #pp.pprint(seq_tree_seq)
        yield td.proto_tools.serialized_message_to_tree(PROTO_PACKAGE_NAME + '.' + PROTO_CLASS, seq_tree_seq.SerializeToString())


def main(unused_argv):
    lex_size = FLAGS.pad_embeddings_to_size
    embedding_dim = 300

    if not os.path.isdir(FLAGS.logdir):
        os.makedirs(FLAGS.logdir)

    # We retrieve our checkpoint fullpath
    checkpoint = tf.train.get_checkpoint_state(FLAGS.logdir)

    #print('load data_mapping from: ' + FLAGS.train_data_path + '.mapping ...')
    #data_maps = pickle.load(open(FLAGS.train_data_path + '.mapping', "rb"))

    # load corpus data
    print('load corpus data from: '+FLAGS.train_data_path + '.data ...')
    seq_data = np.load(FLAGS.train_data_path + '.data')
    print('calc children ...')
    children, roots = preprocessing.children_and_roots(seq_data)

    current_max_depth = 1
    train_iterator = iterator_sequence_trees(FLAGS.train_data_path, current_max_depth, seq_data, children,
                                             FLAGS.sample_count)

    if checkpoint is None:
        # prepare initial gloVe vectors
        print('load embeddings from: ' + FLAGS.train_dict_file + ' ...')
        embeddings_np = np.load(FLAGS.train_dict_file)
        assert lex_size >= embeddings_np.shape[0], 'pad_embeddings_to_size: ' + lex_size \
                                                   +' has to be bigger than or equal to the count of embeddings read ' \
                                                    'from file ' + FLAGS.train_dict_file
        embeddings_padded = np.lib.pad(embeddings_np, ((0, lex_size - embeddings_np.shape[0]), (0, 0)), 'mean')

    with tf.Graph().as_default():
        with tf.device(tf.train.replica_device_setter(FLAGS.ps_tasks)):
            embed_w = tf.Variable(tf.constant(0.0, shape=[lex_size, embedding_dim]), trainable=True, name='embeddings')
            embedding_placeholder = tf.placeholder(tf.float32, [lex_size, embedding_dim])
            embedding_init = embed_w.assign(embedding_placeholder)

            trainer = model_fold.SequenceTreeEmbeddingSequence(embed_w)

            softmax_correct = trainer.softmax_correct
            loss = trainer.loss
            train_op = trainer.train_op
            global_step = trainer.global_step

            # collect important variables
            scoring_vars = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope=model_fold.DEFAULT_SCORING_SCOPE)

            saver = tf.train.Saver()
            with tf.Session() as sess:
                if checkpoint is None:
                    # exclude embedding, will be initialized afterwards
                    init_vars = [v for v in tf.global_variables() if v != embed_w]
                    tf.variables_initializer(init_vars).run()
                    print('init embeddings with external vectors...')
                    sess.run(embedding_init, feed_dict={embedding_placeholder: embeddings_padded})
                else:
                    input_checkpoint = checkpoint.model_checkpoint_path
                    print('restore model from: '+input_checkpoint)
                    saver.restore(sess, input_checkpoint)

                for _ in xrange(FLAGS.max_steps):
                    batch = [next(train_iterator) for _ in xrange(FLAGS.batch_size)]
                    #batch = list(parse_iterator(
                    #    [([u'Hallo.'], 0)],
                    #    nlp, preprocessing.process_sentence3, data_maps))

                    fdict = trainer.build_feed_dict(batch)
                    _, step, loss_v, smax_correct = sess.run([train_op, global_step, loss, softmax_correct], feed_dict=fdict)
                    #print(loss_v)
                    print('step=%d: loss=%f' % (step, loss_v))
                    #print(smax_correct)
                saver.save(sess, os.path.join(FLAGS.logdir, 'model.ckpt-'+str(step)))


if __name__ == '__main__':
    ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
    td.proto_tools.map_proto_source_tree_path('', ROOT_DIR)
    td.proto_tools.import_proto_file(PROTO_FILE_NAME)
    tf.app.run()