from __future__ import print_function
import csv
import os
from sys import maxsize

import pickle
import tensorflow as tf
import numpy as np

import spacy

import constants
import corpus
import preprocessing
import sequence_node_sequence_pb2
import tools
import random
from multiprocessing import Pool
import fnmatch
import ntpath
import re

tf.flags.DEFINE_string(
    'corpus_data_input_train', '/home/arne/devel/ML/data/corpora/WIKIPEDIA/documents_utf8_filtered_20pageviews.csv', #'/home/arne/devel/ML/data/corpora/WIKIPEDIA/wikipedia-23886057.csv',#'/home/arne/devel/ML/data/corpora/WIKIPEDIA/documents_utf8_filtered_20pageviews.csv', # '/home/arne/devel/ML/data/corpora/SICK/sick_train/SICK_train.txt',
    'The path to the SICK train data file.')
#tf.flags.DEFINE_string(
#    'corpus_data_input_test', '/home/arne/devel/ML/data/corpora/SICK/sick_test_annotated/SICK_test_annotated.txt',
#    'The path to the SICK test data file.')
tf.flags.DEFINE_string(
    'corpus_data_output_dir', '/media/arne/WIN/Users/Arne/ML/data/corpora/wikipedia',#'data/corpora/wikipedia',
    'The path to the output data files (samples, embedding vectors, mappings).')
tf.flags.DEFINE_string(
    'corpus_data_output_fn', 'WIKIPEDIA',
    'Base filename of the output data files (samples, embedding vectors, mappings).')
tf.flags.DEFINE_string(
    'init_dict_filename', None, #'/media/arne/WIN/Users/Arne/ML/data/corpora/wikipedia/process_sentence7/WIKIPEDIA_articles1000_maxdepth10',#None, #'data/nlp/spacy/dict',
    'The path to embedding and mapping files (without extension) to reuse them for the new corpus.')
tf.flags.DEFINE_integer(
    'max_articles', 10000,
    'How many articles to read.')
tf.flags.DEFINE_integer(
    'article_batch_size', 250,
    'How many articles to process in one batch.')
tf.flags.DEFINE_integer(
    'max_depth', 10,
    'The maximal depth of the sequence trees.')
tf.flags.DEFINE_integer(
    'count_threshold', 2,
    'Change data types which occur less then count_threshold times to UNKNOWN')
#tf.flags.DEFINE_integer(
#    'sample_count', 14,
#    'Amount of samples per tree. This excludes the correct tree.')
tf.flags.DEFINE_string(
    'sentence_processor', 'process_sentence7', #'process_sentence8',#'process_sentence3',
    'Defines which NLP features are taken into the embedding trees.')
tf.flags.DEFINE_string(
    'tree_mode',
    None,
    #'aggregate',
    #'sequence',
    'How to structure the tree. '
     + '"sequence" -> parents point to next token, '
     + '"aggregate" -> parents point to an added, artificial token (TERMINATOR) in the end of the token sequence,'
     + 'None -> use parsed dependency tree')

FLAGS = tf.flags.FLAGS


def articles_from_csv_reader(filename, max_articles=100, skip=0):
    csv.field_size_limit(maxsize)
    print('parse', max_articles, 'articles...')
    with open(filename, 'rb') as csvfile:
        reader = csv.DictReader(csvfile, fieldnames=['article-id', 'content'])
        i = 0
        for row in reader:
            if skip > 0:
                skip -= 1
                continue
            if i >= max_articles:
                break
            if (i * 10) % max_articles == 0:
                # sys.stdout.write("progress: %d%%   \r" % (i * 100 / max_rows))
                # sys.stdout.flush()
                print('read article:', row['article-id'], '... ', i * 100 / max_articles, '%')
            i += 1
            content = row['content'].decode('utf-8')
            # cut the title (is separated by two spaces from main content)
            yield content.split('  ', 1)[1]


@tools.fn_timer
def convert_wikipedia(in_filename, out_filename, init_dict_filename, sentence_processor, parser, #mapping, vecs,
                      max_articles=10000, max_depth=10, batch_size=100, tree_mode=None):
    parent_dir = os.path.abspath(os.path.join(out_filename, os.pardir))
    out_base_name = ntpath.basename(out_filename)
    if not os.path.isfile(out_filename+'.data') \
            or not os.path.isfile(out_filename + '.parent')\
            or not os.path.isfile(out_filename + '.mapping')\
            or not os.path.isfile(out_filename + '.vecs') \
            or not os.path.isfile(out_filename + '.depth') \
            or not os.path.isfile(out_filename + '.count'):

        if parser is None:
            print('load spacy ...')
            parser = spacy.load('en')
            parser.pipeline = [parser.tagger, parser.entity, parser.parser]
        if init_dict_filename is not None:
            print('initialize vecs and mapping from files ...')
            vecs, mapping = corpus.create_or_read_dict(init_dict_filename, parser.vocab)
            print('dump embeddings to: ' + out_filename + '.vecs ...')
            vecs.dump(out_filename + '.vecs')
        else:
            vecs, mapping = corpus.create_or_read_dict(out_filename, parser.vocab)
        # parse
        seq_data, seq_parents, seq_depths, mapping = parse_articles(out_filename, parent_dir, in_filename, parser,
                                                                    mapping, sentence_processor, max_depth,
                                                                    max_articles, batch_size, tree_mode)
        # sort and filter vecs/mappings by counts
        seq_data, mapping, vecs, counts = preprocessing.sort_embeddings(seq_data, mapping, vecs,
                                                                        count_threshold=FLAGS.count_threshold)
        # write out vecs, mapping and tsv containing strings
        corpus.write_dict(out_path, mapping, vecs, parser.vocab, constants.vocab_manual)
        print('dump data to: ' + out_path + '.data ...')
        seq_data.dump(out_path + '.data')
        print('dump counts to: ' + out_path + '.count ...')
        counts.dump(out_path + '.count')
    else:
        print('load depths from file: ' + out_filename + '.depth ...')
        seq_depths = np.load(out_filename+'.depth')

    preprocessing.calc_depths_collected(out_filename, parent_dir, max_depth, seq_depths)
    preprocessing.rearrange_children_indices(out_filename, parent_dir, max_depth, max_articles, batch_size)
    #preprocessing.concat_children_indices(out_filename, parent_dir, max_depth)

    print('load and concatenate child indices batches ...')
    for current_depth in range(1, max_depth + 1):
        if not os.path.isfile(out_filename + '.children.depth' + str(current_depth)):
            preprocessing.merge_numpy_batch_files(out_base_name + '.children.depth' + str(current_depth), parent_dir)

    return parser


def parse_articles(out_path, parent_dir, in_filename, parser, mapping, sentence_processor, max_depth, max_articles, batch_size, tree_mode):
    out_fn = ntpath.basename(out_path)

    print('parse articles ...')
    child_idx_offset = 0
    for offset in range(0, max_articles, batch_size):
        # all or none: otherwise the mapping lacks entries!
        #if not careful or not os.path.isfile(out_path + '.data.batch' + str(offset)) \
        #        or not os.path.isfile(out_path + '.parent.batch' + str(offset)) \
        #        or not os.path.isfile(out_path + '.depth.batch' + str(offset)) \
        #        or not os.path.isfile(out_path + '.children.batch' + str(offset)):
        current_seq_data, current_seq_parents, current_idx_tuples, current_seq_depths = preprocessing.read_data_2(
            articles_from_csv_reader,
            sentence_processor, parser, mapping,
            args={
                'filename': in_filename,
                'max_articles': min(batch_size, max_articles),
                'skip': offset
            },
            max_depth=max_depth,
            batch_size=batch_size,
            tree_mode=tree_mode,
            child_idx_offset=child_idx_offset)
        print('dump data, parents, depths and child indices for offset=' + str(offset) + ' ...')
        current_seq_data.dump(out_path + '.data.batch' + str(offset))
        current_seq_parents.dump(out_path + '.parent.batch' + str(offset))
        current_seq_depths.dump(out_path + '.depth.batch' + str(offset))
        current_idx_tuples.dump(out_path + '.children.batch' + str(offset))
        child_idx_offset += len(current_seq_data)
        #if careful:
        #   print('dump mappings to: ' + out_path + '.mapping ...')
        #   with open(out_path + '.mapping', "wb") as f:
        #       pickle.dump(mapping, f)
        #else:
        #    current_seq_data = np.load(out_path + '.data.batch' + str(offset))
        #    child_idx_offset += len(current_seq_data)

    seq_data = preprocessing.merge_numpy_batch_files(out_fn+'.data', parent_dir)
    seq_parents = preprocessing.merge_numpy_batch_files(out_fn + '.parent', parent_dir)
    seq_depths = preprocessing.merge_numpy_batch_files(out_fn + '.depth', parent_dir)

    print('parsed data size: '+str(len(seq_data)))

    return seq_data, seq_parents, seq_depths, mapping


if __name__ == '__main__':
    sentence_processor = getattr(preprocessing, FLAGS.sentence_processor)
    out_dir = os.path.abspath(os.path.join(FLAGS.corpus_data_output_dir, sentence_processor.func_name))
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)

    out_path = os.path.join(out_dir, FLAGS.corpus_data_output_fn)
    if FLAGS.tree_mode is not None:
        out_path = out_path + '_' + FLAGS.tree_mode

    out_path = out_path + '_articles' + str(FLAGS.max_articles)
    out_path = out_path + '_maxdepth' + str(FLAGS.max_depth)

    print('output base file name: '+out_path)

    nlp = None
    nlp = convert_wikipedia(FLAGS.corpus_data_input_train,
                                           out_path,
                                           FLAGS.init_dict_filename,
                                           sentence_processor,
                                           nlp,
                                           #mapping,
                                           #vecs,
                                           max_articles=FLAGS.max_articles,
                                           max_depth=FLAGS.max_depth,
                                           #sample_count=FLAGS.sample_count,
                                           batch_size=FLAGS.article_batch_size)
    #print('len(mapping): '+str(len(mapping)))

    #print('parse train data ...')
    #convert_sick(FLAGS.corpus_data_input_train,
    #             out_path + '.train',
    #             sentence_processor,
    #             nlp,
    #             mapping,
    #             FLAGS.corpus_size,
    #             FLAGS.tree_mode)

    #print('parse test data ...')
    #convert_sick(FLAGS.corpus_data_input_test,
    #             out_path + '.test',
    #             sentence_processor,
    #             nlp,
    #             mapping,
    #             FLAGS.corpus_size,
    #             FLAGS.tree_mode)
